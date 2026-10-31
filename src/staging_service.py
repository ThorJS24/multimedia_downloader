import logging
import time
import uuid
from pathlib import Path
from typing import List, Optional

from src.config_loader import get_downloads_path
from src.settings_service import SettingsService
from src.db import get_db_connection, record_audit
from src.file_organizer import FileOrganizer
from src.link_parser import LinkParser
from src.metadata_service import MetadataService
from src.plugin_engine import DownloadArchive

logger = logging.getLogger("StagingService")


class StagingService:
    """
    Import staging and dry-run preview engine.
    Allows users to inspect tracks, target file paths, collision statuses,
    and archive history before committing jobs to the download queue.
    """

    @classmethod
    def create_staging_preview(
        cls,
        raw_input: str,
        template: Optional[str] = None,
        output_format: Optional[str] = None,
        collision_strategy: Optional[str] = None
    ) -> dict:
        cfg = SettingsService.get_all()
        naming_cfg = cfg["naming"]

        template = template or naming_cfg.get("template", "{artist} - {title}.{ext}")
        output_format = output_format or cfg["audio"]["output_format"]
        collision_strategy = collision_strategy or naming_cfg.get("collision_strategy", "skip")
        base_dir = get_downloads_path()

        # Parse links
        parsed_links = LinkParser.parse_multiline(raw_input, deduplicate=True, resolve_redirects=True)
        meta_service = MetadataService()

        tracks_to_stage = []
        for plink in parsed_links:
            if not plink.is_valid:
                continue

            if plink.kind == "track":
                meta = meta_service.resolve_item("track", plink.item_id)
                if meta:
                    tracks_to_stage.append(meta)
            elif plink.kind in ("playlist", "album"):
                collection = meta_service.resolve_item(plink.kind, plink.item_id)
                col_name = collection.get("name", "Collection")
                for t in collection.get("tracks", []):
                    t["playlist_name"] = col_name
                    t["collection_name"] = col_name
                    t["collection_id"] = plink.item_id
                    t["is_playlist"] = True
                    tracks_to_stage.append(t)
            elif plink.kind == "search":
                raw = plink.raw
                if " - " in raw:
                    parts = raw.split(" - ", 1)
                    artist, title = parts[0].strip(), parts[1].strip()
                else:
                    artist, title = "Unknown Artist", raw.strip()
                tracks_to_stage.append({
                    "id": f"search_{uuid.uuid4().hex[:8]}",
                    "title": title,
                    "artists": [artist],
                    "album": "Singles",
                    "duration_ms": 0,
                    "isrc": ""
                })

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        staged_items = []
        new_count = 0
        collision_count = 0
        archived_count = 0

        conn = get_db_connection()

        folder_org = naming_cfg.get("folder_organization", "playlist")
        for t in tracks_to_stage:
            tid = t.get("id", f"track_{uuid.uuid4().hex[:8]}")
            target_path, should_skip = FileOrganizer.resolve_output_path(
                base_dir=base_dir,
                template=template,
                meta=t,
                output_format=output_format,
                collision_strategy=collision_strategy,
                folder_organization=folder_org
            )

            is_archived = DownloadArchive.is_archived(tid)
            if is_archived:
                archived_count += 1

            if target_path.exists():
                collision_count += 1
                collision_status = "COLLISION_SKIP" if should_skip else "COLLISION_OVERWRITE_OR_RENAME"
            else:
                new_count += 1
                collision_status = "NEW_DESTINATION"

            status = "WILL_SKIP" if should_skip else "READY"

            item_dict = {
                "id": f"stg_{uuid.uuid4().hex[:10]}",
                "batch_id": batch_id,
                "track_id": tid,
                "title": t.get("title", "Untitled"),
                "artists": ", ".join(t.get("artists", ["Unknown Artist"])) if isinstance(t.get("artists"), list) else str(t.get("artists")),
                "album": t.get("album", "Singles"),
                "duration_ms": t.get("duration_ms", 0),
                "target_path": str(target_path),
                "collision_status": collision_status,
                "is_archived": is_archived,
                "status": status,
                "created_at": time.time(),
                "raw_meta": t
            }
            staged_items.append(item_dict)

            # Persist staging item to SQLite
            with conn:
                conn.execute(
                    """
                    INSERT INTO staging_items (id, batch_id, track_id, title, artists, album, duration_ms, target_path, collision_status, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_dict["id"],
                        batch_id,
                        tid,
                        item_dict["title"],
                        item_dict["artists"],
                        item_dict["album"],
                        item_dict["duration_ms"],
                        item_dict["target_path"],
                        collision_status,
                        status,
                        item_dict["created_at"]
                    )
                )

        record_audit("STAGING", f"Staged {len(staged_items)} tracks in batch '{batch_id}'")

        return {
            "batch_id": batch_id,
            "total_items": len(staged_items),
            "summary": {
                "new": new_count,
                "collisions": collision_count,
                "archived": archived_count,
                "ready": len([i for i in staged_items if i["status"] == "READY"])
            },
            "items": staged_items
        }

    @classmethod
    def stage_local_file(
        cls,
        file_path: Path,
        event_type: str = "CREATED",
        folder_id: Optional[str] = None,
        batch_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Safely stages a detected local audio file from the folder watcher into staging_items.
        Extracts tags, computes or queries hash, identifies duplicates/collisions,
        and marks for explicit user review without modifying the library or downloading anything.
        """
        from src.library_indexer import LibraryIndexer

        file_path = Path(file_path).resolve()
        batch_id = batch_id or f"batch_watch_{time.strftime('%Y%m%d_%H%M%S')}"
        conn = get_db_connection()
        now = time.time()

        # Check if already staged in this batch or pending
        cur = conn.execute(
            "SELECT id, status FROM staging_items WHERE source_path = ? AND batch_id = ?",
            (str(file_path), batch_id)
        )
        existing = cur.fetchone()
        if existing and existing["status"] in ("READY", "STAGED", "REVIEW_REMOVAL"):
            return None  # Already staged

        is_removal = (event_type.upper() in ("DELETED", "REMOVED"))
        title = file_path.stem
        artists = "Unknown Artist"
        album = "Unknown Album"
        duration_ms = 0
        sha256_hash = ""

        if is_removal:
            # File no longer on disk. Check library_files for existing metadata
            cur = conn.execute("SELECT title, artist, album, duration_sec, sha256 FROM library_files WHERE path = ?", (str(file_path),))
            lib_row = cur.fetchone()
            if lib_row:
                title = lib_row["title"] or title
                artists = lib_row["artist"] or artists
                album = lib_row["album"] or album
                duration_ms = int((lib_row["duration_sec"] or 0) * 1000)
                sha256_hash = lib_row["sha256"] or ""
            collision_status = "REMOVED_FROM_DISK"
            status = "REVIEW_REMOVAL"
            detection_reason = "REMOVED_LOCAL_FILE"
        else:
            if not file_path.exists():
                return None
            try:
                meta, tech, tag_status, issues = LibraryIndexer.inspect_file_metadata(file_path)
                title = meta.get("title") or file_path.stem
                artists = meta.get("artist") or "Unknown Artist"
                album = meta.get("album") or "Unknown Album"
                duration_ms = int((tech.get("duration_sec") or 0) * 1000)
            except Exception as e:
                logger.warning(f"Metadata extraction failed for {file_path}: {e}")

            # Check library_files for existing record and SHA-256
            cur = conn.execute("SELECT mtime, size_bytes, sha256 FROM library_files WHERE path = ?", (str(file_path),))
            lib_row = cur.fetchone()
            try:
                st = file_path.stat()
                file_size = st.st_size
                mtime = st.st_mtime
            except OSError:
                return None

            if lib_row and lib_row["mtime"] == mtime and lib_row["size_bytes"] == file_size and lib_row["sha256"]:
                sha256_hash = lib_row["sha256"]
            else:
                sha256_hash = LibraryIndexer.compute_file_sha256(file_path)

            # Check collisions
            if lib_row:
                if lib_row["sha256"] == sha256_hash:
                    collision_status = "EXISTS_INDEXED"
                    status = "WILL_SKIP"
                    detection_reason = "UNCHANGED_INDEXED_FILE"
                else:
                    collision_status = "MODIFIED_IN_PLACE"
                    status = "READY"
                    detection_reason = "MODIFIED_LOCAL_FILE"
            else:
                dup_cur = conn.execute("SELECT path FROM library_files WHERE sha256 = ? LIMIT 1", (sha256_hash,))
                dup_row = dup_cur.fetchone()
                if dup_row:
                    collision_status = "DUPLICATE_CONTENT_EXISTS"
                    status = "READY"
                    detection_reason = "DUPLICATE_LOCAL_FILE"
                else:
                    collision_status = "NEW_DESTINATION"
                    status = "READY"
                    detection_reason = "NEW_LOCAL_FILE"

        track_id = f"local_{sha256_hash[:16]}" if sha256_hash else f"local_{uuid.uuid4().hex[:12]}"

        # Prevent duplicate staging rows for identical source_path
        cur = conn.execute(
            """
            SELECT id FROM staging_items
            WHERE source_type = 'WATCHER' AND source_path = ? AND status IN ('READY', 'REVIEW_REMOVAL', 'WILL_SKIP')
            LIMIT 1
            """,
            (str(file_path),)
        )
        existing_stg = cur.fetchone()
        if existing_stg:
            item_id = existing_stg["id"]
            with conn:
                conn.execute(
                    """
                    UPDATE staging_items SET
                        batch_id = ?, track_id = ?, title = ?, artists = ?, album = ?, duration_ms = ?,
                        target_path = ?, collision_status = ?, status = ?, detection_reason = ?
                    WHERE id = ?
                    """,
                    (
                        batch_id, track_id, title, artists, album, duration_ms,
                        str(file_path), collision_status, status, detection_reason, item_id
                    )
                )
        else:
            item_id = f"stg_watch_{uuid.uuid4().hex[:10]}"
            with conn:
                conn.execute(
                    """
                    INSERT INTO staging_items (
                        id, batch_id, track_id, title, artists, album, duration_ms,
                        target_path, collision_status, status, created_at,
                        source_type, source_path, detection_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id, batch_id, track_id, title, artists, album, duration_ms,
                        str(file_path), collision_status, status, now,
                        "WATCHER", str(file_path), detection_reason
                    )
                )

        record_audit("WATCHER", f"Staged intake item '{title}' ({detection_reason}) in batch '{batch_id}'")

        return {
            "id": item_id,
            "batch_id": batch_id,
            "track_id": track_id,
            "title": title,
            "artists": artists,
            "album": album,
            "duration_ms": duration_ms,
            "target_path": str(file_path),
            "collision_status": collision_status,
            "status": status,
            "source_type": "WATCHER",
            "detection_reason": detection_reason
        }

    @classmethod
    def get_pending_watcher_items(cls) -> List[dict]:
        conn = get_db_connection()
        cur = conn.execute(
            """
            SELECT id, batch_id, track_id, title, artists, album, duration_ms, target_path,
                   collision_status, status, created_at, source_type, source_path, detection_reason
            FROM staging_items
            WHERE source_type = 'WATCHER' AND status IN ('READY', 'REVIEW_REMOVAL')
            ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]

    @classmethod
    def dismiss_staging_item(cls, item_id: str) -> bool:
        conn = get_db_connection()
        with conn:
            cur = conn.execute("UPDATE staging_items SET status = 'DISMISSED' WHERE id = ?", (item_id,))
            return cur.rowcount > 0

    @classmethod
    def commit_staging_batch(cls, batch_id: str, queue_manager, excluded_ids: Optional[List[str]] = None) -> int:
        from src.library_indexer import LibraryIndexer

        conn = get_db_connection()
        cur = conn.execute(
            "SELECT * FROM staging_items WHERE batch_id = ? AND status IN ('READY', 'REVIEW_REMOVAL')",
            (batch_id,)
        )
        rows = cur.fetchall()
        if not rows:
            return 0

        excluded_set = set(excluded_ids or [])
        download_tracks = []
        committed_ids = []
        watcher_processed = 0

        for r in rows:
            if r["id"] in excluded_set or r["track_id"] in excluded_set:
                with conn:
                    conn.execute("UPDATE staging_items SET status = 'EXCLUDED' WHERE id = ?", (r["id"],))
                continue

            # Check if local watcher intake item vs remote download job
            source_type = r["source_type"] if "source_type" in r.keys() else "DOWNLOAD"
            if source_type == "WATCHER":
                target_p = Path(r["target_path"])
                detection_reason = r["detection_reason"] if "detection_reason" in r.keys() else ""
                if detection_reason == "REMOVED_LOCAL_FILE":
                    with conn:
                        conn.execute("DELETE FROM library_files WHERE path = ?", (str(target_p),))
                    committed_ids.append(r["id"])
                    watcher_processed += 1
                else:
                    if target_p.exists() and LibraryIndexer.index_single_file(target_p):
                        committed_ids.append(r["id"])
                        watcher_processed += 1
                    else:
                        with conn:
                            conn.execute("UPDATE staging_items SET status = 'FAILED' WHERE id = ?", (r["id"],))
            else:
                download_tracks.append({
                    "id": r["track_id"],
                    "title": r["title"],
                    "artists": r["artists"].split(", ") if r["artists"] else ["Unknown"],
                    "album": r["album"],
                    "duration_ms": r["duration_ms"]
                })
                committed_ids.append(r["id"])

        enqueued_downloads = 0
        if download_tracks:
            enqueued_downloads = queue_manager.add_batch(
                download_tracks,
                collection_id=batch_id,
                collection_name=f"Staged Batch {batch_id[:8]}"
            )

        if committed_ids:
            with conn:
                conn.executemany(
                    "UPDATE staging_items SET status = 'COMMITTED' WHERE id = ?",
                    [(cid,) for cid in committed_ids]
                )

        total_committed = enqueued_downloads + watcher_processed
        record_audit("STAGING", f"Committed batch '{batch_id}' — enqueued/indexed {total_committed} items (excluded {len(excluded_set)}).")
        return total_committed
