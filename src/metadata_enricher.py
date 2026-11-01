"""Online tag and artwork lookup for tracks with missing metadata (MusicBrainz + Cover Art Archive).

Only used when you ask for it: nothing is sent anywhere until you press the button, and what is sent is the track's
title, artist and length. A lookup runs as a cancellable background job (MusicBrainz asks for at most one request per
second) and only *proposes* changes; the proposals stay on the server, so applying can only write what was shown.
Writing goes through LibraryIntelligence.apply_metadata_repair (backup, verification and rollback).
"""
import difflib
import logging
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from src import http_client
from src.library_intelligence import LibraryIntelligence
from src.version import __version__

logger = logging.getLogger("MetadataEnricher")

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"
USER_AGENT = f"SpotifyDownloaderStudio/{__version__} ( https://github.com/ThorJS24/multimedia_downloader )"
MIN_SCORE = 90                 # MusicBrainz's own 0-100 match score
DURATION_TOLERANCE_SEC = 5.0
MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_TRACKS_PER_RUN = 200
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PLACEHOLDERS = {"", "unknown", "unknown artist", "unknown album", "singles", "untitled"}
FIELD_LABELS = {"title": "Title", "artist": "Artist", "album": "Album", "year": "Year", "track_number": "Track number"}


class LookupUnavailable(RuntimeError):
    """The online service could not be reached or refused us; the run stops instead of failing track by track."""


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in PLACEHOLDERS


def _norm(text: str) -> str:
    return LibraryIntelligence.normalize_string(text or "")


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace('"', '\\"')


def artist_credit(recording: Dict[str, Any]) -> str:
    return "".join(f"{c.get('name', '')}{c.get('joinphrase', '')}" for c in recording.get("artist-credit", [])).strip()


class MusicBrainzClient:
    """Thin, polite client: one request per second, a real User-Agent, errors turned into LookupUnavailable."""
    min_interval = 1.1
    _lock = threading.Lock()
    _last_call = 0.0

    @classmethod
    def _throttle(cls) -> None:
        with cls._lock:
            wait = cls.min_interval - (time.monotonic() - cls._last_call)
            if wait > 0:
                time.sleep(wait)
            cls._last_call = time.monotonic()

    @classmethod
    def search_recordings(cls, title: str, artist: str = "") -> List[Dict[str, Any]]:
        query = f'recording:"{_escape(title)}"'
        if artist:
            query += f' AND artist:"{_escape(artist)}"'
        cls._throttle()
        try:
            resp = http_client.get(f"{MB_BASE}/recording", params={"query": query, "fmt": "json", "limit": 5},
                                   headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=12, retries=2)
        except requests.RequestException as e:
            raise LookupUnavailable("Cannot reach MusicBrainz. Check your internet connection and try again.") from e
        if resp.status_code != 200:
            raise LookupUnavailable(f"MusicBrainz is busy or unavailable right now (HTTP {resp.status_code}). Try again in a few minutes.")
        try:
            return list(resp.json().get("recordings", []))
        except ValueError as e:
            raise LookupUnavailable("MusicBrainz sent a reply the app could not read.") from e

    @classmethod
    def cover_available(cls, release_id: str) -> bool:
        if not UUID_RE.match(release_id or ""):
            return False
        try:
            resp = http_client.head(f"{CAA_BASE}/release/{release_id}/front-500", headers={"User-Agent": USER_AGENT},
                                    timeout=10, retries=1, allow_redirects=True)
        except requests.RequestException:
            return False
        return resp.status_code == 200

    @classmethod
    def fetch_cover(cls, release_id: str) -> Optional[bytes]:
        if not UUID_RE.match(release_id or ""):
            return None
        try:
            resp = http_client.get(f"{CAA_BASE}/release/{release_id}/front-500", headers={"User-Agent": USER_AGENT},
                                   timeout=15, retries=1, allow_redirects=True)
        except requests.RequestException:
            logger.warning("Cover art download failed for release %s", release_id, exc_info=True)
            return None
        data = resp.content
        if resp.status_code != 200 or not data or len(data) > MAX_COVER_BYTES:
            return None
        if "image/jpeg" not in resp.headers.get("Content-Type", "image/jpeg"):   # the tag writer embeds JPEG
            return None
        return data


def choose_release(releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prefer an official album, then the earliest release date."""
    def rank(r: Dict[str, Any]) -> tuple:
        official = r.get("status") == "Official"
        album = (r.get("release-group") or {}).get("primary-type") == "Album"
        return (0 if official and album else 1 if official else 2, r.get("date") or "9999")
    return min(releases, key=rank) if releases else None


def pick_match(recordings: List[Dict[str, Any]], title: str, artist: str, duration_sec: float) -> Optional[Dict[str, Any]]:
    """The best recording that agrees with what we know about the file; None when nothing is convincing."""
    best: Optional[Dict[str, Any]] = None
    for rec in recordings:
        score = int(rec.get("score", 0))
        if score < MIN_SCORE:
            continue
        title_sim = _similar(rec.get("title", ""), title)
        if title_sim < 0.85:
            continue
        credit = artist_credit(rec)
        if not is_blank(artist) and _similar(credit, artist) < 0.75 and _norm(artist) not in _norm(credit) and _norm(credit) not in _norm(artist):
            continue
        length_ms = rec.get("length")
        if duration_sec and length_ms and abs(length_ms / 1000.0 - duration_sec) > DURATION_TOLERANCE_SEC:
            continue
        confidence = round((score / 100.0 + title_sim) / 2.0, 2)
        if best is None or confidence > best["confidence"]:
            best = {"recording": rec, "release": choose_release(rec.get("releases", [])), "confidence": confidence}
    return best


def _track_position(release: Dict[str, Any]) -> Optional[int]:
    for medium in release.get("media", []) or []:
        for track in medium.get("track", []) or []:
            number = str(track.get("number", "")).strip()
            if number.isdigit():
                return int(number)
    return None


def build_proposal(row: Dict[str, Any], match: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn a match into per-field proposals. Gaps are pre-selected; changes to values you already have are not."""
    rec, release, confidence = match["recording"], match["release"] or {}, match["confidence"]
    proposed = {
        "title": rec.get("title", ""),
        "artist": artist_credit(rec),
        "album": release.get("title", ""),
        "year": (release.get("date") or "")[:4],
        "track_number": _track_position(release) or "",
    }
    fields = []
    for name, new in proposed.items():
        if is_blank(new):
            continue
        current = row.get(name)
        if not is_blank(current) and _norm(str(current)) == _norm(str(new)):
            continue
        fills_gap = is_blank(current)
        fields.append({"field": name, "label": FIELD_LABELS[name], "current": "" if is_blank(current) else str(current),
                       "proposed": str(new), "fills_gap": fills_gap, "selected": fills_gap and confidence >= 0.9})
    release_id = release.get("id", "")
    art = None
    if not row.get("has_artwork") and UUID_RE.match(release_id) and MusicBrainzClient.cover_available(release_id):
        art = {"available": True, "selected": True}
    if not fields and art is None:
        return None
    return {"path": row["path"], "filename": row.get("filename") or "", "confidence": confidence, "release_id": release_id,
            "total_tracks": release.get("track-count") or 0, "matched": f"{proposed['artist']} - {proposed['title']}",
            "fields": fields, "art": art}


class MetadataEnricher:
    """Background lookup job plus the apply step. One job at a time."""
    _lock = threading.Lock()
    _state: Dict[str, Any] = {"id": None, "running": False, "cancelled": False, "total": 0, "done": 0, "items": [],
                              "counts": {}, "error": None, "finished_at": None}
    _thread: Optional[threading.Thread] = None

    @classmethod
    def _rows(cls, filter_type: str, limit: int) -> List[Dict[str, Any]]:
        page = LibraryIntelligence.get_metadata_audit_items(filter_type, limit=limit, offset=0)
        return list(page["items"])

    @classmethod
    def start(cls, filter_type: str = "all", limit: int = 50) -> str:
        limit = max(1, min(int(limit), MAX_TRACKS_PER_RUN))
        with cls._lock:
            if cls._state["running"]:
                raise RuntimeError("A lookup is already running")
            run_id = uuid.uuid4().hex[:10]
            cls._state = {"id": run_id, "running": True, "cancelled": False, "total": 0, "done": 0, "items": [],
                          "counts": {"no_match": 0, "no_title": 0, "unreadable": 0, "already_ok": 0}, "error": None, "finished_at": None}
        cls._thread = threading.Thread(target=cls._run, args=(run_id, filter_type, limit), daemon=True, name="MetadataEnricher")
        cls._thread.start()
        return run_id

    @classmethod
    def _run(cls, run_id: str, filter_type: str, limit: int) -> None:
        try:
            rows = cls._rows(filter_type, limit)
            with cls._lock:
                cls._state["total"] = len(rows)
            for row in rows:
                with cls._lock:
                    if cls._state["id"] != run_id or cls._state["cancelled"]:
                        return
                cls._look_up(run_id, row)
                with cls._lock:
                    cls._state["done"] += 1
        except LookupUnavailable as e:
            with cls._lock:
                cls._state["error"] = str(e)
        except Exception as e:
            logger.error("Metadata lookup failed", exc_info=True)
            with cls._lock:
                cls._state["error"] = f"The lookup stopped unexpectedly: {e}"
        finally:
            with cls._lock:
                if cls._state["id"] == run_id:
                    cls._state["running"] = False
                    cls._state["finished_at"] = time.time()

    @classmethod
    def _look_up(cls, run_id: str, row: Dict[str, Any]) -> None:
        def count(key: str) -> None:
            with cls._lock:
                cls._state["counts"][key] += 1

        if row.get("tag_status") in ("UNREADABLE", "CORRUPT"):
            return count("unreadable")
        title, artist = row.get("title") or "", row.get("artist") or ""
        if is_blank(title) or is_blank(artist):               # fall back to what the file name says
            try:
                guess = {f["field"]: f["proposed"] for f in LibraryIntelligence.generate_proposed_metadata(row["path"])["fields"]}
            except (OSError, PermissionError, ValueError):
                guess = {}
            title = title if not is_blank(title) else guess.get("title", "")
            artist = artist if not is_blank(artist) else guess.get("artist", "")
        if is_blank(title):
            return count("no_title")
        match = pick_match(MusicBrainzClient.search_recordings(title, "" if is_blank(artist) else artist), title, artist,
                           float(row.get("duration_sec") or 0))
        if match is None:
            return count("no_match")
        proposal = build_proposal(row, match)
        if proposal is None:
            return count("already_ok")
        with cls._lock:
            if cls._state["id"] == run_id:
                cls._state["items"].append(proposal)

    @classmethod
    def status(cls) -> Dict[str, Any]:
        with cls._lock:
            return {**cls._state, "items": list(cls._state["items"]), "counts": dict(cls._state["counts"])}

    @classmethod
    def cancel(cls) -> bool:
        with cls._lock:
            was_running = cls._state["running"]
            cls._state["cancelled"] = True
            return was_running

    @classmethod
    def apply(cls, selections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Write the chosen proposals. Values come from the stored lookup, never from the request."""
        with cls._lock:
            items = {i["path"]: i for i in cls._state["items"]}
        applied, failed, no_art, done_paths = 0, [], 0, []
        for sel in selections:
            item = items.get(sel.get("path", ""))
            if item is None:
                failed.append({"path": sel.get("path", ""), "error": "That file is not part of the last lookup"})
                continue
            wanted = set(sel.get("fields") or [])
            updates: Dict[str, Any] = {f["field"]: f["proposed"] for f in item["fields"] if f["field"] in wanted}
            if "track_number" in updates and item.get("total_tracks"):
                updates["total_tracks"] = item["total_tracks"]
            cover = None
            if sel.get("art") and item.get("art") and item["art"]["available"]:
                cover = MusicBrainzClient.fetch_cover(item["release_id"])
                if cover is None:
                    no_art += 1
            if not updates and cover is None:
                continue
            try:
                LibraryIntelligence.apply_metadata_repair(item["path"], updates, cover_bytes=cover)
            except (RuntimeError, OSError, PermissionError) as e:
                failed.append({"path": item["path"], "error": str(e)})
                continue
            applied += 1
            done_paths.append(item["path"])
        with cls._lock:
            cls._state["items"] = [i for i in cls._state["items"] if i["path"] not in done_paths]
        return {"applied": applied, "failed": failed, "art_unavailable": no_art}
