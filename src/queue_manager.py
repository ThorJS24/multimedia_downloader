import enum
import logging
import os
import random
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src import http_client
from src.config_loader import get_downloads_path, get_temp_path, PROJECT_ROOT
from src.settings_service import SettingsService
from src.db import get_db_connection
from src.file_organizer import FileOrganizer
from src.lyrics_service import LyricsService
from src.match_engine import MatchCandidate, MatchEngine
from src.media_processor import MediaProcessor
from src.metadata_service import MetadataService
from src.plugin_engine import DownloadArchive, PluginEngine
from src.spotify_direct import SpotifyDirectClient
from src.tag_writer import TagWriter

logger = logging.getLogger("QueueManager")


class JobStage(str, enum.Enum):
    QUEUED = "QUEUED"
    FETCHING_METADATA = "FETCHING_METADATA"
    MATCHING_SOURCE = "MATCHING_SOURCE"
    DOWNLOADING = "DOWNLOADING"
    CONVERTING = "CONVERTING"
    TAGGING = "TAGGING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class QueueJob:
    def __init__(
        self,
        job_id: str,
        track_id: str,
        collection_id: str = "",
        collection_name: str = "",
        title: str = "",
        artists: Optional[List[str]] = None,
        album: str = "",
        duration_ms: int = 0,
        cover_url: str = "",
        isrc: str = "",
        year: str = "",
        provider: str = "spotify",
        provider_item_id: str = "",
        canonical_url: str = "",
        is_alternative: bool = False,
        original_spotify_id: str = "",
        capabilities: Optional[dict] = None
    ):
        self.id = job_id
        self.track_id = track_id
        self.collection_id = collection_id
        self.collection_name = collection_name
        self.title = title or track_id
        self.artists = artists or ["Unknown Artist"]
        self.album = album
        self.duration_ms = duration_ms
        self.cover_url = cover_url
        self.isrc = isrc
        self.year = year
        self.provider = provider or "spotify"
        self.provider_item_id = provider_item_id or track_id
        self.canonical_url = canonical_url or ""
        self.is_alternative = bool(is_alternative)
        self.original_spotify_id = original_spotify_id or ""
        self.capabilities = capabilities or {}
        self.stage = JobStage.QUEUED
        self.status = "QUEUED"
        self.progress = 0.0
        self.speed = "0 KB/s"
        self.eta = "--"
        self.output_path = ""
        self.error = ""
        self.match_confidence = 1.0
        self.match_source = f"{self.provider.upper()} Direct" if self.provider != "spotify" else "Spotify AP CDN"
        self.added_at = time.time()
        self.completed_at: Optional[float] = None
        self.attempts = 0
        self.retry_not_before = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "track_id": self.track_id,
            "collection_id": self.collection_id,
            "collection_name": self.collection_name,
            "title": self.title,
            "artists": self.artists,
            "album": self.album,
            "duration_ms": self.duration_ms,
            "cover_url": self.cover_url,
            "isrc": self.isrc,
            "year": self.year,
            "provider": self.provider,
            "provider_item_id": self.provider_item_id,
            "canonical_url": self.canonical_url,
            "is_alternative": self.is_alternative,
            "original_spotify_id": self.original_spotify_id,
            "capabilities": self.capabilities,
            "stage": self.stage.value,
            "status": self.status,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "output_path": self.output_path,
            "error": self.error,
            "match_confidence": self.match_confidence,
            "match_source": self.match_source,
            "added_at": self.added_at,
            "completed_at": self.completed_at,
            "attempts": self.attempts
        }


class QueueManager:
    """
    Queue manager and concurrency balancer with persistent SQLite state.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.jobs: List[QueueJob] = []
        self.active_workers: List[threading.Thread] = []
        self.max_workers = 1
        self.is_paused = False
        self.is_running = True

        self.direct_client = SpotifyDirectClient()
        self.metadata_service = MetadataService()

        # Load persisted unfinished jobs from SQLite
        self._restore_jobs()

        # Start master dispatcher thread
        self.dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            daemon=True,
            name="MasterQueueDispatcher"
        )
        self.dispatcher_thread.start()

    def _restore_jobs(self):
        try:
            conn = get_db_connection()
            if not SettingsService.get_all()["general"]["restore_queue_on_startup"]:
                with conn:
                    cancelled = conn.execute(
                        "UPDATE jobs SET status = 'CANCELLED', stage = 'CANCELLED' "
                        "WHERE status NOT IN ('COMPLETED', 'SKIPPED', 'CANCELLED')"
                    ).rowcount
                if cancelled:
                    logger.info("restore_queue_on_startup is off: cancelled %d unfinished jobs", cancelled)
                return
            cur = conn.execute("SELECT * FROM jobs WHERE status NOT IN ('COMPLETED', 'SKIPPED', 'CANCELLED') ORDER BY added_at ASC")
            for r in cur.fetchall():
                row_keys = r.keys() if hasattr(r, "keys") else []
                provider = r["provider"] if "provider" in row_keys else "spotify"
                provider_item_id = r["provider_item_id"] if "provider_item_id" in row_keys else r["track_id"]
                canonical_url = r["canonical_url"] if "canonical_url" in row_keys else ""
                is_alt = bool(r["is_alternative"]) if "is_alternative" in row_keys and r["is_alternative"] else False
                orig_sp_id = r["original_spotify_id"] if "original_spotify_id" in row_keys else ""

                j = QueueJob(
                    job_id=r["id"],
                    track_id=r["track_id"],
                    collection_id=r["collection_id"] or "",
                    collection_name=r["collection_name"] or "",
                    title=r["title"] or "",
                    artists=r["artists"].split(", ") if r["artists"] else ["Unknown"],
                    album=r["album"] or "",
                    duration_ms=r["duration_ms"] or 0,
                    cover_url=r["cover_url"] or "",
                    isrc=r["isrc"] or "",
                    year=r["year"] or "",
                    provider=provider or "spotify",
                    provider_item_id=provider_item_id or r["track_id"],
                    canonical_url=canonical_url or "",
                    is_alternative=is_alt,
                    original_spotify_id=orig_sp_id or ""
                )
                j.stage = JobStage.QUEUED
                j.status = "QUEUED"
                j.attempts = (r["attempts"] or 0) if "attempts" in row_keys else 0
                self.jobs.append(j)
            if self.jobs:
                logger.info(f"Restored {len(self.jobs)} pending jobs from SQLite")
        except Exception as e:
            logger.warning(f"Could not restore jobs from DB: {e}")

    def add_job(self, track_info: dict, collection_id: str = "", collection_name: str = "") -> QueueJob:
        with self._lock:
            track_id = str(track_info.get("id") or track_info.get("provider_item_id") or "")
            job_id = f"job_{track_id}_{int(time.time() * 1000)}"
            c_id = track_info.get("collection_id") or collection_id or ""
            c_name = track_info.get("collection_name") or track_info.get("playlist_name") or collection_name or ""
            job = QueueJob(
                job_id=job_id,
                track_id=track_id,
                collection_id=c_id,
                collection_name=c_name,
                title=track_info.get("title", ""),
                artists=track_info.get("artists", ["Unknown"]),
                album=track_info.get("album", ""),
                duration_ms=track_info.get("duration_ms", 0),
                cover_url=track_info.get("cover_url", ""),
                isrc=track_info.get("isrc", ""),
                year=track_info.get("year", ""),
                provider=track_info.get("provider", "spotify"),
                provider_item_id=track_info.get("provider_item_id", track_id),
                canonical_url=track_info.get("canonical_url", ""),
                is_alternative=bool(track_info.get("is_alternative", False)),
                original_spotify_id=track_info.get("original_spotify_id", ""),
                capabilities=track_info.get("capabilities", {})
            )
            self.jobs.append(job)
            self._save_job_to_db(job)
            return job

    def add_batch(self, tracks: List[dict], collection_id: str = "", collection_name: str = "") -> int:
        with self._lock:
            existing_ids = {j.track_id for j in self.jobs if j.status not in ["FAILED", "CANCELLED"]}
            added = 0
            for t in tracks:
                if t["id"] not in existing_ids:
                    c_id = t.get("collection_id") or collection_id or ""
                    c_name = t.get("collection_name") or t.get("playlist_name") or collection_name or ""
                    self.add_job(t, c_id, c_name)
                    existing_ids.add(t["id"])
                    added += 1
            logger.info(f"Enqueued {added} tracks in batch")
            return added

    def pause(self):
        with self._lock:
            self.is_paused = True
            logger.info("Queue paused")

    def resume(self):
        with self._lock:
            self.is_paused = False
            logger.info("Queue resumed")

    def cancel_all(self):
        with self._lock:
            for j in self.jobs:
                if j.status == "QUEUED":
                    j.stage = JobStage.CANCELLED
                    j.status = "CANCELLED"
                    self._save_job_to_db(j)
            logger.info("All queued jobs cancelled")

    def cancel_job(self, job_id: str):
        with self._lock:
            for j in self.jobs:
                if j.id == job_id:
                    j.stage = JobStage.CANCELLED
                    j.status = "CANCELLED"
                    self._save_job_to_db(j)
                    logger.info(f"Job {job_id} cancelled")
                    break

    def retry_job(self, job_id: str):
        with self._lock:
            for j in self.jobs:
                if j.id == job_id:
                    j.stage = JobStage.QUEUED
                    j.status = "QUEUED"
                    j.progress = 0.0
                    j.error = ""
                    j.attempts = 0
                    j.retry_not_before = 0.0
                    self._save_job_to_db(j)
                    logger.info(f"Re-queued job {job_id}")
                    break

    def remove_job(self, job_id: str):
        with self._lock:
            self.jobs = [j for j in self.jobs if j.id != job_id]
            conn = get_db_connection()
            with conn:
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def reorder_job(self, job_id: str, direction: str):
        with self._lock:
            for idx, j in enumerate(self.jobs):
                if j.id == job_id and j.status == "QUEUED":
                    if direction == "up" and idx > 0:
                        self.jobs[idx], self.jobs[idx - 1] = self.jobs[idx - 1], self.jobs[idx]
                    elif direction == "down" and idx < len(self.jobs) - 1:
                        self.jobs[idx], self.jobs[idx + 1] = self.jobs[idx + 1], self.jobs[idx]
                    break

    def move_job(self, job_id: str, before_id: Optional[str] = None) -> bool:
        """Move a waiting job so it sits directly before `before_id` (or at the end when None)."""
        with self._lock:
            job = next((j for j in self.jobs if j.id == job_id and j.status == "QUEUED"), None)
            if job is None or job_id == before_id:
                return False
            self.jobs.remove(job)
            idx = next((i for i, j in enumerate(self.jobs) if j.id == before_id), None) if before_id else None
            if idx is None:
                self.jobs.append(job)
            else:
                self.jobs.insert(idx, job)
            return True

    def clear_finished(self):
        with self._lock:
            self.jobs = [j for j in self.jobs if j.status not in ["COMPLETED", "SKIPPED", "CANCELLED"]]

    def retry_all_failed(self) -> int:
        with self._lock:
            count = 0
            for j in self.jobs:
                if j.status == "FAILED":
                    j.stage = JobStage.QUEUED
                    j.status = "QUEUED"
                    j.progress = 0.0
                    j.error = ""
                    j.attempts = 0
                    j.retry_not_before = 0.0
                    self._save_job_to_db(j)
                    count += 1
            logger.info(f"Re-queued {count} failed jobs")
            return count

    def clear_failed(self) -> int:
        with self._lock:
            failed_ids = [j.id for j in self.jobs if j.status == "FAILED"]
            self.jobs = [j for j in self.jobs if j.status != "FAILED"]
            if failed_ids:
                conn = get_db_connection()
                with conn:
                    conn.executemany("DELETE FROM jobs WHERE id = ?", [(fid,) for fid in failed_ids])
            logger.info(f"Cleared {len(failed_ids)} failed jobs")
            return len(failed_ids)

    def batch_retry(self, job_ids: List[str]) -> int:
        with self._lock:
            target_ids = set(job_ids)
            count = 0
            for j in self.jobs:
                if j.id in target_ids:
                    j.stage = JobStage.QUEUED
                    j.status = "QUEUED"
                    j.progress = 0.0
                    j.error = ""
                    j.attempts = 0
                    j.retry_not_before = 0.0
                    self._save_job_to_db(j)
                    count += 1
            return count

    def batch_cancel(self, job_ids: List[str]) -> int:
        with self._lock:
            target_ids = set(job_ids)
            count = 0
            for j in self.jobs:
                if j.id in target_ids and j.status == "QUEUED":
                    j.stage = JobStage.CANCELLED
                    j.status = "CANCELLED"
                    self._save_job_to_db(j)
                    count += 1
            return count

    def batch_remove(self, job_ids: List[str]) -> int:
        with self._lock:
            target_ids = set(job_ids)
            self.jobs = [j for j in self.jobs if j.id not in target_ids]
            conn = get_db_connection()
            with conn:
                conn.executemany("DELETE FROM jobs WHERE id = ?", [(jid,) for jid in target_ids])
            return len(target_ids)

    def batch_reorder(self, job_ids: List[str], target: str) -> int:
        with self._lock:
            target_ids = set(job_ids)
            selected = [j for j in self.jobs if j.id in target_ids]
            remaining = [j for j in self.jobs if j.id not in target_ids]
            if target == "top":
                self.jobs = selected + remaining
            else:
                self.jobs = remaining + selected
            return len(selected)

    def batch_update(self, job_ids: List[str], updates: dict) -> int:
        with self._lock:
            target_ids = set(job_ids)
            count = 0
            for j in self.jobs:
                if j.id in target_ids:
                    if updates.get("album"):
                        j.album = updates["album"].strip()
                    if updates.get("collection_name"):
                        j.collection_name = updates["collection_name"].strip()
                    if updates.get("requeue"):
                        j.stage = JobStage.QUEUED
                        j.status = "QUEUED"
                        j.progress = 0.0
                        j.error = ""
                    self._save_job_to_db(j)
                    count += 1
            return count

    def update_job_metadata(self, job_id: str, updates: dict) -> Optional[dict]:
        with self._lock:
            for j in self.jobs:
                if j.id == job_id:
                    if "title" in updates and updates["title"]:
                        j.title = updates["title"].strip()
                    if "artists" in updates:
                        if isinstance(updates["artists"], list):
                            j.artists = updates["artists"]
                        elif isinstance(updates["artists"], str):
                            j.artists = [a.strip() for a in updates["artists"].split(",") if a.strip()]
                    if "album" in updates:
                        j.album = updates["album"].strip()
                    if "collection_name" in updates:
                        j.collection_name = updates["collection_name"].strip()
                    if updates.get("retry", False):
                        j.stage = JobStage.QUEUED
                        j.status = "QUEUED"
                        j.progress = 0.0
                        j.error = ""
                    self._save_job_to_db(j)
                    return j.to_dict()
            return None

    def set_concurrency(self, workers: int):
        with self._lock:
            self.max_workers = max(1, min(workers, 4))
            logger.info(f"Concurrency set to {self.max_workers}")

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "is_paused": self.is_paused,
                "max_workers": self.max_workers,
                "counts": {
                    "total": len(self.jobs),
                    "queued": sum(1 for j in self.jobs if j.status == "QUEUED"),
                    "active": sum(1 for j in self.jobs if j.status in ["ACTIVE", "DOWNLOADING", "CONVERTING", "TAGGING"]),
                    "completed": sum(1 for j in self.jobs if j.status == "COMPLETED"),
                    "skipped": sum(1 for j in self.jobs if j.status == "SKIPPED"),
                    "failed": sum(1 for j in self.jobs if j.status == "FAILED"),
                    "cancelled": sum(1 for j in self.jobs if j.status == "CANCELLED")
                },
                "jobs": [j.to_dict() for j in self.jobs]
            }

    def _save_job_to_db(self, j: QueueJob):
        try:
            import json
            caps_json = json.dumps(j.capabilities) if j.capabilities else "{}"
            conn = get_db_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, track_id, collection_id, collection_name, title, artists, album, duration_ms,
                        cover_url, isrc, year, stage, status, progress, speed, eta, output_path, error,
                        match_confidence, match_source, added_at, completed_at,
                        provider, provider_item_id, canonical_url, is_alternative, original_spotify_id, capabilities_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        stage = excluded.stage,
                        status = excluded.status,
                        progress = excluded.progress,
                        speed = excluded.speed,
                        eta = excluded.eta,
                        output_path = excluded.output_path,
                        error = excluded.error,
                        completed_at = excluded.completed_at,
                        provider = excluded.provider,
                        provider_item_id = excluded.provider_item_id,
                        canonical_url = excluded.canonical_url,
                        is_alternative = excluded.is_alternative,
                        original_spotify_id = excluded.original_spotify_id,
                        capabilities_json = excluded.capabilities_json
                    """,
                    (
                        j.id, j.track_id, j.collection_id, j.collection_name, j.title,
                        ", ".join(j.artists), j.album, j.duration_ms, j.cover_url,
                        j.isrc, j.year, j.stage.value, j.status, j.progress, j.speed,
                        j.eta, j.output_path, j.error, j.match_confidence, j.match_source,
                        j.added_at, j.completed_at,
                        j.provider, j.provider_item_id, j.canonical_url, 1 if j.is_alternative else 0,
                        j.original_spotify_id, caps_json
                    )
                )
                conn.execute("UPDATE jobs SET attempts = ? WHERE id = ?", (j.attempts, j.id))
        except Exception as e:
            logger.debug(f"Error saving job to DB: {e}")

    MAX_FINISHED_IN_MEMORY = 1000

    def _trim_finished(self):
        """Keep the in-memory job list bounded; finished jobs stay available in SQLite."""
        finished = [j for j in self.jobs if j.status in ("COMPLETED", "SKIPPED", "CANCELLED")]
        excess = len(finished) - self.MAX_FINISHED_IN_MEMORY
        if excess > 0:
            drop = {id(j) for j in sorted(finished, key=lambda j: j.completed_at or j.added_at)[:excess]}
            self.jobs = [j for j in self.jobs if id(j) not in drop]

    def _dispatcher_loop(self):
        ticks = 0
        while self.is_running:
            time.sleep(0.4)
            ticks += 1
            if ticks % 150 == 0:  # roughly once a minute
                with self._lock:
                    self._trim_finished()
            if self.is_paused:
                continue

            with self._lock:
                self.active_workers = [w for w in self.active_workers if w.is_alive()]
                if len(self.active_workers) >= self.max_workers:
                    continue

                next_job = None
                now = time.time()
                for j in self.jobs:
                    if j.status == "QUEUED" and j.retry_not_before <= now:
                        next_job = j
                        break

                if next_job:
                    next_job.status = "ACTIVE"
                    next_job.stage = JobStage.FETCHING_METADATA
                    self._save_job_to_db(next_job)

                    w = threading.Thread(
                        target=self._process_job,
                        args=(next_job,),
                        daemon=True,
                        name=f"Worker-{next_job.track_id}"
                    )
                    self.active_workers.append(w)
                    w.start()

    # ------------------------------------------------------------------ pipeline
    #
    # A job runs through explicit stages. Each stage takes the shared _Run context and returns
    # True to continue or False when the job reached a terminal state (skipped / failed /
    # cancelled) and must stop. Unexpected exceptions are handled once, in _process_job, which
    # either schedules a retry or marks the job failed.

    MIN_FREE_BYTES = 50 * 1024 * 1024
    MIN_MATCH_CONFIDENCE = 0.70
    NON_RETRYABLE_MARKERS = ("premium", "authentication required", "not logged in", "credential", "login")

    def _process_job(self, job: QueueJob):
        run = _Run(job=job, cfg=SettingsService.get_all())
        stages = (
            self._stage_check_disk,
            self._stage_metadata,
            self._stage_match,
            self._stage_resolve_path,
            self._stage_download,
            self._stage_transcode,
            self._stage_tag,
            self._stage_verify,
        )
        try:
            for stage in stages:
                if job.status == "CANCELLED":
                    return
                if not stage(run):
                    return
            self._stage_complete(run)
        except PermissionError as exc:
            self._fail(job, str(exc), notify=True)
            logger.error("Permission error on job %s: %s", job.id, exc)
        except Exception as exc:
            self._retry_or_fail(run, exc)
        finally:
            run.discard_raw_audio()

    # --- terminal helpers

    def _fail(self, job: QueueJob, message: str, notify: bool = True):
        job.stage = JobStage.FAILED
        job.status = "FAILED"
        job.error = message
        self._save_job_to_db(job)
        if notify:
            PluginEngine.dispatch("on_download_failed", self._event_payload(job))

    def _retry_or_fail(self, run: "_Run", exc: Exception):
        job, message = run.job, str(exc)
        retry_limit = run.cfg["performance"]["retry_limit"]
        retryable = not any(marker in message.lower() for marker in self.NON_RETRYABLE_MARKERS)
        if retryable and job.attempts < retry_limit:
            job.attempts += 1
            rate_limited = "429" in message or "too many requests" in message.lower()
            wait = http_client.backoff_seconds(job.attempts, 30.0 * job.attempts if rate_limited else None)
            job.retry_not_before = time.time() + wait
            job.stage = JobStage.QUEUED
            job.status = "QUEUED"
            job.progress = 0.0
            job.speed = f"Retry {job.attempts}/{retry_limit} in {wait:.0f}s"
            job.error = message
            self._save_job_to_db(job)
            logger.warning("Job %s failed (%s); retry %d/%d in %.0fs", job.id, message, job.attempts, retry_limit, wait)
            return
        self._fail(job, message)
        logger.error("Failed job %s: %s", job.id, message)

    @staticmethod
    def _event_payload(job: QueueJob) -> dict:
        return {
            "track_id": job.track_id,
            "title": job.title,
            "artists": job.artists,
            "album": job.album,
            "provider": job.provider,
            "output_path": job.output_path,
            "error": job.error,
        }

    def _advance(self, job: QueueJob, stage: JobStage, progress: float):
        job.stage = stage
        job.progress = progress
        self._save_job_to_db(job)

    # --- stages

    def _stage_check_disk(self, run: "_Run") -> bool:
        _, _, free_bytes = shutil.disk_usage(PROJECT_ROOT)
        if free_bytes < self.MIN_FREE_BYTES:
            self._fail(run.job, "Insufficient disk space on target drive (< 50MB)", notify=False)
            return False
        return True

    def _stage_metadata(self, run: "_Run") -> bool:
        job = run.job
        job.speed = "Resolving..."
        self._advance(job, JobStage.FETCHING_METADATA, 10.0)

        # A user-chosen alternative (YouTube / YouTube Music) is tagged from the Spotify track it stands in for;
        # its own provider id means nothing to Spotify. Other non-Spotify jobs just use their stored fields.
        lookup_id: Optional[str] = job.track_id
        if job.provider != "spotify":
            lookup_id = job.original_spotify_id if (job.is_alternative and job.original_spotify_id) else None
        try:
            if lookup_id is None:
                raise LookupError("no Spotify id for this job; using the job's own fields")
            meta = self.metadata_service.resolve_item("track", lookup_id)
        except Exception as meta_err:
            logger.warning("Metadata resolution failed for %s (%s); using cached job fields.", job.track_id, meta_err)
            meta = {
                "id": job.track_id,
                "title": job.title or "Unknown Track",
                "artists": job.artists or ["Unknown Artist"],
                "album": job.album or "",
                "cover_url": job.cover_url or "",
                "isrc": job.isrc or "",
                "year": job.year or "",
                "duration_ms": job.duration_ms or 0,
            }

        # Keep an authentic album the job already had if the resolver returned nothing/"Singles"
        if job.album and (not meta.get("album") or meta.get("album") == "Singles"):
            meta["album"] = job.album
        if not meta.get("album_artist"):
            artists_list = meta.get("artists") or job.artists or ["Unknown Artist"]
            meta["album_artist"] = artists_list[0] if isinstance(artists_list, list) and artists_list else str(artists_list)

        job.title = meta.get("title", job.title)
        job.artists = meta.get("artists", job.artists)
        job.album = meta.get("album", job.album)
        job.cover_url = meta.get("cover_url", job.cover_url)
        job.isrc = meta.get("isrc", job.isrc)
        job.year = meta.get("year", job.year)
        job.duration_ms = meta.get("duration_ms", job.duration_ms)
        run.meta = meta
        return True

    def _stage_match(self, run: "_Run") -> bool:
        job = run.job
        self._advance(job, JobStage.MATCHING_SOURCE, 25.0)
        candidate = MatchCandidate(
            candidate_id=job.track_id,
            title=job.title,
            artists=job.artists,
            duration_ms=job.duration_ms,
            source="Spotify AP CDN",
            isrc=job.isrc,
        )
        job.match_confidence = MatchEngine.evaluate_match(run.meta, candidate)
        if not candidate.is_acceptable:
            self._fail(
                job,
                f"Match rejected: Insufficient confidence ({job.match_confidence:.2f} < {self.MIN_MATCH_CONFIDENCE:.2f}). "
                "Avoided silent substitution.",
            )
            logger.warning("Job %s rejected: %s", job.id, job.error)
            return False
        return True

    def _stage_resolve_path(self, run: "_Run") -> bool:
        job, meta, cfg = run.job, run.meta, run.cfg
        naming = cfg["naming"]
        meta["playlist_name"] = job.collection_name or ""
        meta["collection_name"] = job.collection_name or ""
        meta["collection_id"] = job.collection_id or ""
        if job.collection_id or (job.collection_name and job.collection_name.lower() not in ("downloads", "singles", "unknown", "")):
            meta["is_playlist"] = True

        run.target_path, should_skip = FileOrganizer.resolve_output_path(
            base_dir=get_downloads_path(),
            template=naming["template"],
            meta=meta,
            output_format=cfg["audio"]["output_format"],
            collision_strategy=naming["collision_strategy"],
            create_parent=True,
            folder_organization=naming["folder_organization"],
        )
        if should_skip:
            job.stage = JobStage.SKIPPED
            job.status = "SKIPPED"
            job.progress = 100.0
            job.output_path = str(run.target_path)
            job.completed_at = time.time()
            self._save_job_to_db(job)
            logger.info("Skipped existing: %s", Path(job.output_path).name)
            return False
        return True

    def _stage_download(self, run: "_Run") -> bool:
        job, audio = run.job, run.cfg["audio"]
        self._advance(job, JobStage.DOWNLOADING, 40.0)
        started = time.time()

        if job.provider in ("ytmusic", "youtube"):
            # Only ever reached through an explicit, user-chosen alternative match.
            job.speed = f"Fetching from {job.provider.upper()}"
            from src.compliant_audio import CompliantAudioProvider
            run.raw_audio_path, _ = CompliantAudioProvider.fetch_audio_for_provider_item(
                provider=job.provider,
                provider_item_id=job.provider_item_id,
                canonical_url=job.canonical_url,
                output_format=audio["output_format"],
                bitrate=audio["mp3_bitrate"],
                return_path=True,
            )
            job.match_source = f"{job.provider.upper()} Explicit Selection"
        else:
            # Rule A: a Spotify job is never silently turned into a YouTube/YouTube Music download.
            job.speed = "Streaming Spotify CDN"
            try:
                run.raw_audio_path = get_temp_path() / f"dl_{os.getpid()}_{job.id}.ogg"
                self.direct_client.download_track_to_file(job.track_id, run.raw_audio_path, quality=audio["audio_quality"])
                job.match_source = "Spotify AP CDN"
            except Exception as direct_err:
                raise RuntimeError(
                    f"Spotify audio stream unavailable: {direct_err}. "
                    "Per Rule A, automatic fallback to other sources is prohibited. "
                    "Use 'Find alternatives' to explicitly select a YouTube Music or YouTube match."
                ) from direct_err

        elapsed = max(0.1, time.time() - started)
        job.speed = f"{run.raw_audio_path.stat().st_size / 1024.0 / elapsed:.0f} KB/s"
        return True

    def _stage_transcode(self, run: "_Run") -> bool:
        job, audio = run.job, run.cfg["audio"]
        self._advance(job, JobStage.CONVERTING, 70.0)
        MediaProcessor.transcode(
            raw_audio_bytes=None,
            raw_audio_path=run.raw_audio_path,
            output_path=run.require_target(),
            output_format=audio["output_format"],
            bitrate=audio["mp3_bitrate"],
            normalize_loudness=audio["normalize_loudness"],
        )
        run.discard_raw_audio()  # the temp download is no longer needed once transcoded
        return True

    def _stage_tag(self, run: "_Run") -> bool:
        job, audio, meta = run.job, run.cfg["audio"], run.meta
        self._advance(job, JobStage.TAGGING, 85.0)

        cover_bytes = None
        if job.cover_url:
            try:
                res = http_client.get(job.cover_url, timeout=10, retries=1)
                if res.status_code == 200:
                    cover_bytes = res.content
            except Exception as cover_err:
                logger.debug("Cover download failed for %s: %s", job.id, cover_err)

        if audio["embed_lyrics"] and not meta.get("lyrics"):
            meta["lyrics"] = LyricsService.fetch(
                artist=(job.artists or [""])[0],
                title=job.title,
                album=job.album,
                duration_sec=(job.duration_ms or 0) / 1000.0 or None,
            )

        TagWriter.apply_tags(
            file_path=run.require_target(),
            meta=meta,
            cover_image_bytes=cover_bytes,
            save_cover_art_file=audio["save_cover_art"],
        )
        return True

    def _stage_verify(self, run: "_Run") -> bool:
        self._advance(run.job, JobStage.VERIFYING, 95.0)
        verified, message = MediaProcessor.verify_output(run.require_target())
        if not verified:
            raise RuntimeError(f"Verification failure: {message}")
        return True

    def _stage_complete(self, run: "_Run"):
        job, target = run.job, run.require_target()
        job.stage = JobStage.COMPLETED
        job.status = "COMPLETED"
        job.progress = 100.0
        job.speed = "Done"
        job.eta = "0s"
        job.error = ""
        job.output_path = str(target)
        job.completed_at = time.time()
        self._save_job_to_db(job)
        logger.info("Job completed: %s -> %s", job.title, target.name)

        DownloadArchive.record_completed(
            resource_id=job.track_id,
            resource_type="track",
            title=job.title,
            artist=", ".join(job.artists),
            file_path=str(target),
        )
        PluginEngine.dispatch("on_download_complete", self._event_payload(job))

        try:
            from src.library_indexer import LibraryIndexer
            LibraryIndexer.index_single_file(target)
        except Exception as idx_err:
            logger.debug("Auto-index failed for %s: %s", target, idx_err)

        # Polite pacing between tracks
        max_delay = run.cfg["performance"]["rate_limit_max_delay"]
        if max_delay > 0:
            delay = round(random.uniform(run.cfg["performance"]["rate_limit_min_delay"], max_delay), 2)
            job.eta = f"Pacing {delay}s"
            time.sleep(delay)


@dataclass
class _Run:
    """Mutable state shared by the pipeline stages of one job."""
    job: QueueJob
    cfg: dict
    meta: dict = field(default_factory=dict)
    target_path: Optional[Path] = None
    raw_audio_path: Optional[Path] = None   # temp file holding the downloaded stream

    def require_target(self) -> Path:
        if self.target_path is None:
            raise RuntimeError("output path was not resolved before it was needed")
        return self.target_path

    def discard_raw_audio(self) -> None:
        if self.raw_audio_path is not None:
            try:
                self.raw_audio_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove temp download %s", self.raw_audio_path, exc_info=True)
            self.raw_audio_path = None
