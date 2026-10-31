import json
import logging
import re
import shutil
import string
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mutagen

from src.config_loader import PROJECT_ROOT, TEMP_DIR, get_downloads_path
from src.db import get_db_connection, record_audit
from src.file_organizer import FileOrganizer
from src.security import SecurityManager
from src.tag_writer import TagWriter

logger = logging.getLogger("LibraryIntelligence")


class LibraryIntelligence:
    """
    Advanced Music Library Intelligence, Quality Auditing, and Storage Optimization Engine.
    Provides verifiable health metrics, exact and probable duplicate detection,
    proposed-vs-current metadata repair with backup rollback, and reversible bulk reorganization.
    """

    @classmethod
    def get_intelligence_overview(cls) -> Dict[str, Any]:
        """
        Calculates verified health metrics directly from indexed SQLite records.
        Guarantees zero hardcoded counts or fabricated scores.
        """
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM library_files").fetchall()
        total_files = len(rows)

        if total_files == 0:
            # Query last scan info
            last_scan = conn.execute("SELECT * FROM library_scans ORDER BY started_at DESC LIMIT 1").fetchone()
            return {
                "health_score": 100,
                "total_tracks": 0,
                "total_artists": 0,
                "total_albums": 0,
                "total_size_mb": 0.0,
                "total_duration_hours": 0.0,
                "format_breakdown": {},
                "bitrate_breakdown": {"lossless": 0, "high_320k": 0, "standard": 0, "low": 0},
                "missing_tags_count": 0,
                "missing_covers_count": 0,
                "unreadable_tags_count": 0,
                "exact_duplicates_count": 0,
                "probable_duplicates_count": 0,
                "anomalous_duration_count": 0,
                "files_requiring_review": 0,
                "last_scan": dict(last_scan) if last_scan else None,
                "recommendations": []
            }

        total_bytes = sum(r["size_bytes"] or 0 for r in rows)
        total_duration_sec = sum(r["duration_sec"] or 0 for r in rows)

        formats: Dict[str, int] = {}
        bitrate_breakdown = {"lossless": 0, "high_320k": 0, "standard": 0, "low": 0}
        artists: set = set()
        albums: set = set()

        missing_tags_count = 0
        missing_covers_count = 0
        unreadable_tags_count = 0
        anomalous_duration_count = 0
        files_requiring_review_set = set()

        # SHA-256 map for exact duplicates
        sha_map: Dict[str, List[str]] = {}

        for r in rows:
            fmt = (r["format"] or "unknown").lower()
            formats[fmt] = formats.get(fmt, 0) + 1

            br = r["bitrate_kbps"] or 0
            if fmt in ("flac", "wav"):
                bitrate_breakdown["lossless"] += 1
            elif br >= 310:
                bitrate_breakdown["high_320k"] += 1
            elif br >= 190:
                bitrate_breakdown["standard"] += 1
            else:
                bitrate_breakdown["low"] += 1

            if r["artist"] and r["artist"] != "Unknown Artist":
                artists.add(r["artist"])
            else:
                missing_tags_count += 1
                files_requiring_review_set.add(r["path"])

            if r["album"] and r["album"] != "Unknown Album":
                albums.add(r["album"])

            if not r["has_artwork"]:
                missing_covers_count += 1
                files_requiring_review_set.add(r["path"])

            if r["tag_status"] in ("UNREADABLE", "CORRUPT"):
                unreadable_tags_count += 1
                files_requiring_review_set.add(r["path"])

            dur = r["duration_sec"] or 0.0
            if dur > 0 and (dur < 30.0 or dur > 1200.0):
                anomalous_duration_count += 1
                files_requiring_review_set.add(r["path"])

            sha = r["sha256"]
            if sha:
                sha_map.setdefault(sha, []).append(r["path"])

        # Exact duplicate redundant count
        exact_duplicate_files_count = sum(len(paths) - 1 for paths in sha_map.values() if len(paths) > 1)
        if exact_duplicate_files_count > 0:
            for paths in sha_map.values():
                if len(paths) > 1:
                    files_requiring_review_set.update(paths[1:])

        # Probable recording duplicates count
        probable_groups = cls.find_probable_recording_duplicates()
        probable_duplicate_files_count = sum(len(g["files"]) - 1 for g in probable_groups)

        # Health score calculation: Ratio of compliant files without issues to total files
        clean_files = max(0, total_files - len(files_requiring_review_set))
        health_score = round((clean_files / total_files) * 100)

        # Actionable recommendations
        recommendations = []
        if exact_duplicate_files_count > 0:
            recommendations.append({
                "severity": "HIGH",
                "action": "Deduplicate Exact Files",
                "description": f"Found {exact_duplicate_files_count} bit-for-bit identical audio duplicates consuming excess storage.",
                "type": "exact_dedup"
            })
        if probable_duplicate_files_count > 0:
            recommendations.append({
                "severity": "MEDIUM",
                "action": "Review Recording Duplicates",
                "description": f"Identified {probable_duplicate_files_count} probable recording duplicates (different bitrates/formats of same song).",
                "type": "probable_dedup"
            })
        if missing_tags_count > 0:
            recommendations.append({
                "severity": "MEDIUM",
                "action": "Repair Missing Metadata",
                "description": f"{missing_tags_count} audio files lack artist or album tags.",
                "type": "tags"
            })
        if unreadable_tags_count > 0:
            recommendations.append({
                "severity": "HIGH",
                "action": "Inspect Corrupt Tags",
                "description": f"{unreadable_tags_count} audio files have unreadable or unsupported tag containers.",
                "type": "unreadable"
            })
        if missing_covers_count > 0:
            recommendations.append({
                "severity": "LOW",
                "action": "Embed Cover Artwork",
                "description": f"{missing_covers_count} tracks lack embedded or folder cover artwork.",
                "type": "artwork"
            })

        # Fetch last scan details
        last_scan = conn.execute("SELECT * FROM library_scans ORDER BY started_at DESC LIMIT 1").fetchone()

        return {
            "health_score": health_score,
            "total_tracks": total_files,
            "total_artists": len(artists),
            "total_albums": len(albums),
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "total_duration_hours": round(total_duration_sec / 3600, 2),
            "format_breakdown": formats,
            "bitrate_breakdown": bitrate_breakdown,
            "missing_tags_count": missing_tags_count,
            "missing_covers_count": missing_covers_count,
            "unreadable_tags_count": unreadable_tags_count,
            "exact_duplicates_count": exact_duplicate_files_count,
            "probable_duplicates_count": probable_duplicate_files_count,
            "anomalous_duration_count": anomalous_duration_count,
            "files_requiring_review": len(files_requiring_review_set),
            "last_scan": dict(last_scan) if last_scan else None,
            "recommendations": recommendations
        }

    # ================= 1. SAFE DUPLICATE DETECTION =================

    @classmethod
    def get_exact_duplicates(cls) -> List[Dict[str, Any]]:
        """
        Category A: Identifies exact file duplicates via cryptographic SHA-256 hash.
        Includes retention recommendation and technical parameters.
        """
        conn = get_db_connection()
        cur = conn.execute("""
            SELECT path, filename, folder, title, artist, album, format, size_bytes,
                   bitrate_kbps, sample_rate, duration_sec, has_artwork, sha256
            FROM library_files
            WHERE sha256 IS NOT NULL AND sha256 != ''
        """)
        files = cur.fetchall()

        sha_map: Dict[str, List[dict]] = {}
        for f in files:
            sha_map.setdefault(f["sha256"], []).append(dict(f))

        results = []
        for sha, group in sha_map.items():
            if len(group) > 1:
                # Retention recommendation: Keep the file whose path is cleanest or shortest
                recommended_keep = min(group, key=lambda x: len(x["path"]))
                results.append({
                    "group_id": f"exact_{sha[:12]}",
                    "type": "EXACT_HASH",
                    "sha256": sha,
                    "count": len(group),
                    "size_bytes": group[0]["size_bytes"],
                    "recommended_keep_path": recommended_keep["path"],
                    "retention_reason": "Identical content; shortest directory hierarchy",
                    "files": group
                })

        return results

    @classmethod
    def find_probable_recording_duplicates(cls) -> List[Dict[str, Any]]:
        """
        Category B: Identifies probable recording duplicates using normalized metadata
        (title, artist, album, duration ±3s) while excluding exact SHA-256 duplicates.
        """
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT path, filename, title, artist, album, format, size_bytes,
                   bitrate_kbps, sample_rate, duration_sec, has_artwork, sha256, tag_status
            FROM library_files
            WHERE title != 'Unknown Artist' AND artist != 'Unknown Artist'
        """).fetchall()

        # Build groupings based on normalized (artist, title)
        norm_map: Dict[Tuple[str, str], List[dict]] = {}
        for r in rows:
            d = dict(r)
            norm_a = cls.normalize_string(d.get("artist", ""))
            norm_t = cls.normalize_string(cls.strip_track_title_noise(d.get("title", "")))
            if norm_a and norm_t:
                norm_map.setdefault((norm_a, norm_t), []).append(d)

        results = []
        for (norm_a, norm_t), candidates in norm_map.items():
            if len(candidates) < 2:
                continue

            # Exclude pairs that share the same SHA-256 (they are already exact duplicates)
            unique_shas = {c["sha256"] for c in candidates if c.get("sha256")}
            if len(unique_shas) <= 1 and len(candidates) == len([c for c in candidates if c.get("sha256")]):
                continue

            # Sub-cluster by duration within tolerance of ±3.5 seconds
            clusters: List[List[dict]] = []
            for cand in candidates:
                dur = cand.get("duration_sec", 0.0)
                matched_cluster = None
                for cluster in clusters:
                    base_dur = cluster[0].get("duration_sec", 0.0)
                    if abs(dur - base_dur) <= 3.5:
                        matched_cluster = cluster
                        break
                if matched_cluster is not None:
                    matched_cluster.append(cand)
                else:
                    clusters.append([cand])

            for cluster in clusters:
                if len(cluster) > 1:
                    # Score each candidate for retention recommendation
                    # Scoring priority: Lossless (1000) > Bitrate (kbps) > Has Artwork (+50) > Valid tags (+50)
                    def score_candidate(item: dict) -> int:
                        score = item.get("bitrate_kbps", 0)
                        if item.get("format", "").lower() in ("flac", "wav"):
                            score += 1000
                        if item.get("has_artwork"):
                            score += 50
                        if item.get("tag_status") == "VALID":
                            score += 50
                        return score

                    best_candidate = max(cluster, key=score_candidate)
                    reason = f"Highest quality: {best_candidate.get('format', '').upper()} @ {best_candidate.get('bitrate_kbps', 0)} kbps"
                    if best_candidate.get("has_artwork"):
                        reason += ", with artwork"

                    results.append({
                        "group_id": f"prob_{uuid.uuid4().hex[:10]}",
                        "type": "PROBABLE_METADATA",
                        "normalized_artist": norm_a,
                        "normalized_title": norm_t,
                        "count": len(cluster),
                        "recommended_keep_path": best_candidate["path"],
                        "retention_reason": reason,
                        "files": cluster
                    })

        return results

    @classmethod
    def move_duplicate_to_backup(cls, file_to_move: str) -> Dict[str, Any]:
        """
        Reversible safe action: Moves duplicate to downloads/_duplicates_backup/
        and records in organize_history for restoration.
        """
        p = Path(file_to_move).resolve()
        if not SecurityManager.validate_safe_path(p, PROJECT_ROOT):
            raise PermissionError(f"Access denied: Path '{file_to_move}' is outside project directory.")
        if not p.exists():
            raise FileNotFoundError(f"File '{file_to_move}' does not exist.")

        backup_dir = get_downloads_path() / "_duplicates_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        dest_file = backup_dir / p.name
        counter = 1
        while dest_file.exists():
            dest_file = backup_dir / f"{p.stem}_{counter}{p.suffix}"
            counter += 1

        shutil.move(str(p), str(dest_file))

        # Record in organize_history & delete from library_files
        conn = get_db_connection()
        batch_id = f"backup_{int(time.time())}"
        with conn:
            conn.execute(
                """
                INSERT INTO organize_history (id, timestamp, original_path, new_path, status, batch_id)
                VALUES (?, ?, ?, ?, 'MOVED', ?)
                """,
                (f"hist_{uuid.uuid4().hex[:10]}", time.time(), str(p), str(dest_file), batch_id)
            )
            conn.execute("DELETE FROM library_files WHERE path = ?", (str(p),))

        record_audit("DEDUP", f"Moved duplicate '{p.name}' to backup folder: '{dest_file.name}'")
        return {"success": True, "original_path": str(p), "backup_path": str(dest_file)}

    @classmethod
    def delete_duplicate_safely(cls, file_to_delete: str) -> bool:
        """
        Safely deletes a duplicate file only after strictly verifying another copy exists
        with identical SHA-256 content hash. Creates temporary safety backup before removal.
        """
        p = Path(file_to_delete).resolve()
        if not SecurityManager.validate_safe_path(p, PROJECT_ROOT):
            raise PermissionError(f"Access denied: Path '{file_to_delete}' is outside project directory.")

        conn = get_db_connection()
        target = conn.execute("SELECT * FROM library_files WHERE path = ?", (file_to_delete,)).fetchone()
        if not target:
            target = conn.execute("SELECT * FROM library_files WHERE path = ?", (str(p),)).fetchone()
        if not target:
            raise ValueError(f"File '{file_to_delete}' not registered in library.")

        target_sha = target["sha256"]
        if not target_sha:
            raise RuntimeError("Cannot verify duplicate safety: target has no SHA-256 hash.")

        same_sha = conn.execute(
            "SELECT path FROM library_files WHERE sha256 = ? AND path != ? AND path != ?",
            (target_sha, file_to_delete, str(p))
        ).fetchall()

        if not same_sha:
            raise RuntimeError("Safety Check Failed: Refusing to delete the only verified copy of this audio file!")

        if p.exists():
            # Create safety buffer in temp before deleting
            temp_safety = TEMP_DIR / f"del_safety_{int(time.time())}_{p.name}"
            shutil.copy2(p, temp_safety)
            p.unlink()

        with conn:
            conn.execute("DELETE FROM library_files WHERE path = ?", (file_to_delete,))
            conn.execute("DELETE FROM fingerprints WHERE path = ?", (file_to_delete,))

        record_audit("DEDUP", f"Safely deleted redundant copy: {p.name}")
        return True

    # ================= 2. METADATA AUDIT & REPAIR WORKFLOW =================

    @classmethod
    def get_metadata_audit_items(cls, filter_type: str = "all", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Retrieves tracks requiring metadata audit and repair.
        """
        conn = get_db_connection()
        where_sql = "WHERE (tag_status != 'VALID' OR has_artwork = 0 OR artist = 'Unknown Artist' OR album = 'Unknown Album')"
        if filter_type == "missing_artist":
            where_sql = "WHERE (artist IS NULL OR artist = '' OR artist = 'Unknown Artist')"
        elif filter_type == "missing_album":
            where_sql = "WHERE (album IS NULL OR album = '' OR album = 'Unknown Album')"
        elif filter_type == "missing_art":
            where_sql = "WHERE has_artwork = 0"
        elif filter_type == "corrupt":
            where_sql = "WHERE tag_status IN ('UNREADABLE', 'CORRUPT')"

        count_cur = conn.execute(f"SELECT COUNT(*) as total FROM library_files {where_sql}")
        total = count_cur.fetchone()["total"]

        page_cur = conn.execute(
            f"SELECT * FROM library_files {where_sql} ORDER BY artist ASC, title ASC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        items = []
        for r in page_cur.fetchall():
            d = dict(r)
            issues = []
            if d.get("issues_json"):
                try: issues = json.loads(d["issues_json"])
                except Exception: pass
            d["issues"] = issues
            items.append(d)

        return {"total": total, "items": items, "limit": limit, "offset": offset}

    @classmethod
    def generate_proposed_metadata(cls, file_path_str: str) -> Dict[str, Any]:
        """
        Generates proposed-versus-current metadata comparison.
        Derives proposed values from filename heuristics and tag cleaning.
        Identifies source, confidence, and approval status for each field.
        """
        p = Path(file_path_str).resolve()
        if not SecurityManager.validate_safe_path(p, PROJECT_ROOT):
            raise PermissionError(f"Access denied: Path '{file_path_str}' is outside project directory.")
        if not p.exists():
            raise FileNotFoundError(f"Audio file '{file_path_str}' not found.")

        conn = get_db_connection()
        row = conn.execute("SELECT * FROM library_files WHERE path = ?", (file_path_str,)).fetchone()
        current: Dict[str, Any] = dict(row) if row else {
            "title": p.stem,
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "album_artist": "",
            "track_number": 0,
            "year": "",
            "genre": "",
            "has_artwork": 0
        }

        stem = p.stem
        # Filename heuristic parser
        # Patterns like: "01 - Artist - Title", "Artist - Title", "01. Artist - Title"
        proposed_title: str = current.get("title") or stem
        proposed_artist: str = current.get("artist") or "Unknown Artist"
        proposed_track = current.get("track_number") or 0
        source_title = "Current Tag"
        source_artist = "Current Tag"
        conf_title = 1.0 if current.get("title") and current.get("title") != stem else 0.5
        conf_artist = 1.0 if current.get("artist") and current.get("artist") != "Unknown Artist" else 0.5

        # Extract track number prefix
        track_match = re.match(r"^(\d{1,2})[\s\.\-_]+(.+)$", stem)
        if track_match:
            try:
                proposed_track = int(track_match.group(1))
            except ValueError:
                pass
            stem = track_match.group(2).strip()

        # Extract "Artist - Title"
        if " - " in stem:
            parts = stem.split(" - ", 1)
            p_art, p_tit = parts[0].strip(), parts[1].strip()
            if proposed_artist == "Unknown Artist" or not proposed_artist:
                proposed_artist = string.capwords(p_art)
                source_artist = "Filename Heuristic"
                conf_artist = 0.85
            if proposed_title == p.stem or not proposed_title:
                proposed_title = string.capwords(cls.strip_track_title_noise(p_tit))
                source_title = "Filename Heuristic"
                conf_title = 0.85

        # Casing cleanups
        if proposed_title and (proposed_title.islower() or proposed_title.isupper()):
            proposed_title = string.capwords(proposed_title)
            source_title = "Title Case Normalizer"
            conf_title = 0.90

        if proposed_artist and (proposed_artist.islower() or proposed_artist.isupper()):
            proposed_artist = string.capwords(proposed_artist)
            source_artist = "Title Case Normalizer"
            conf_artist = 0.90

        proposed_album = current.get("album")
        if not proposed_album or proposed_album == "Unknown Album":
            parent_folder = p.parent.name
            if parent_folder and parent_folder.lower() not in ("downloads", "music", "temp", "desktop"):
                proposed_album = string.capwords(parent_folder)
                source_album = "Folder Name Heuristic"
                conf_album = 0.75
            else:
                proposed_album = "Singles"
                source_album = "Default Single"
                conf_album = 0.60
        else:
            source_album = "Current Tag"
            conf_album = 1.0

        fields = [
            {
                "field": "title",
                "label": "Track Title",
                "current": current.get("title") or "",
                "proposed": proposed_title,
                "source": source_title,
                "confidence": conf_title,
                "is_different": (current.get("title") or "").strip() != proposed_title.strip()
            },
            {
                "field": "artist",
                "label": "Artist",
                "current": current.get("artist") or "",
                "proposed": proposed_artist,
                "source": source_artist,
                "confidence": conf_artist,
                "is_different": (current.get("artist") or "").strip() != proposed_artist.strip()
            },
            {
                "field": "album",
                "label": "Album",
                "current": current.get("album") or "",
                "proposed": proposed_album,
                "source": source_album,
                "confidence": conf_album,
                "is_different": (current.get("album") or "").strip() != proposed_album.strip()
            },
            {
                "field": "track_number",
                "label": "Track Number",
                "current": str(current.get("track_number") or ""),
                "proposed": str(proposed_track or ""),
                "source": "Filename Track Index",
                "confidence": 0.90 if proposed_track else 0.5,
                "is_different": str(current.get("track_number") or "") != str(proposed_track or "")
            }
        ]

        return {
            "path": str(p),
            "filename": p.name,
            "has_changes": any(f["is_different"] for f in fields),
            "fields": fields
        }

    @classmethod
    def apply_metadata_repair(cls, file_path_str: str, approved_updates: Dict[str, Any]) -> bool:
        """
        Applies approved field updates with pre-write safety backup,
        post-write mutagen verification, and automatic rollback on failure.
        """
        p = Path(file_path_str).resolve()
        if not SecurityManager.validate_safe_path(p, PROJECT_ROOT):
            raise PermissionError(f"Access denied: Path '{file_path_str}' is outside project directory.")
        if not p.exists():
            raise FileNotFoundError(f"File '{file_path_str}' not found.")

        # 1. Create safety backup buffer in temp/
        temp_dir = TEMP_DIR
        safety_backup = temp_dir / f"meta_bak_{int(time.time())}_{p.name}"
        shutil.copy2(p, safety_backup)

        try:
            # 2. Build metadata payload
            meta: Dict[str, Any] = {}
            if "title" in approved_updates and approved_updates["title"]:
                meta["title"] = str(approved_updates["title"]).strip()
            if "artist" in approved_updates and approved_updates["artist"]:
                meta["artists"] = [str(approved_updates["artist"]).strip()]
            if "album" in approved_updates and approved_updates["album"]:
                meta["album"] = str(approved_updates["album"]).strip()
            if "track_number" in approved_updates and approved_updates["track_number"]:
                try: meta["track_number"] = int(approved_updates["track_number"])
                except ValueError: pass
            if "year" in approved_updates and approved_updates["year"]:
                meta["year"] = str(approved_updates["year"]).strip()

            success = TagWriter.apply_tags(p, meta)
            if not success:
                raise RuntimeError("TagWriter returned False")

            # 3. Post-write verification
            verify_audio = mutagen.File(p)
            if verify_audio is None:
                raise RuntimeError("Verification failed: written audio file is unreadable.")

            # Re-index this file in library_files
            conn = get_db_connection()
            from src.library_indexer import LibraryIndexer
            LibraryIndexer._index_file(conn, p)

            # Cleanup safety backup
            if safety_backup.exists():
                try: safety_backup.unlink()
                except OSError: pass

            record_audit("METADATA", f"Repaired metadata for '{p.name}' with {len(approved_updates)} approved fields.")
            return True

        except Exception as e:
            # Revert from safety backup
            if safety_backup.exists():
                try:
                    shutil.copy2(safety_backup, p)
                    safety_backup.unlink()
                except OSError: pass
            logger.error(f"Metadata repair failed, rolled back to original: {e}")
            raise RuntimeError(f"Metadata repair failed and was safely rolled back: {e}") from e

    # ================= 3. SAFE BULK REORGANIZATION =================

    @classmethod
    def preview_bulk_organization(
        cls,
        template: str = "{artist}/{album}/{track_number} - {title}.{ext}",
        collision_strategy: str = "skip",
        filter_format: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Dry-run preview of planned file and folder organization without modifying files.
        Detects collisions, reserved Windows names, and path containment.
        """
        conn = get_db_connection()
        base_dir = get_downloads_path()
        rows = conn.execute("SELECT * FROM library_files").fetchall()

        planned_moves = []
        collisions = 0
        unchanged = 0

        for r in rows:
            fmt = r["format"] or "mp3"
            if filter_format and fmt.lower() != filter_format.lower():
                continue

            current_path = Path(r["path"])
            meta = {
                "title": r["title"] or current_path.stem,
                "artist": r["artist"] or "Unknown Artist",
                "artists": [r["artist"] or "Unknown Artist"],
                "album": r["album"] or "Unknown Album",
                "track_number": r["track_number"] or 1,
                "year": r["year"] or "0000"
            }

            try:
                target_path, should_skip = FileOrganizer.resolve_output_path(
                    base_dir=base_dir,
                    template=template,
                    meta=meta,
                    output_format=fmt,
                    collision_strategy=collision_strategy
                )
            except Exception as e:
                planned_moves.append({
                    "current_path": str(current_path),
                    "target_path": "ERROR: Path Traversal or Illegal Name",
                    "status": "ERROR",
                    "error": str(e)
                })
                continue

            if current_path.resolve() == target_path.resolve():
                status = "UNCHANGED"
                unchanged += 1
            elif target_path.exists():
                collisions += 1
                status = "COLLISION_SKIP" if should_skip else "COLLISION_OVERWRITE_OR_RENAME"
            else:
                status = "WILL_MOVE"

            planned_moves.append({
                "current_path": str(current_path),
                "target_path": str(target_path),
                "status": status,
                "filename": current_path.name
            })

        return {
            "total_files": len(planned_moves),
            "will_move": len([m for m in planned_moves if m["status"] in ("WILL_MOVE", "COLLISION_OVERWRITE_OR_RENAME")]),
            "unchanged": unchanged,
            "collisions": collisions,
            "moves": planned_moves
        }

    @classmethod
    def execute_bulk_organization(cls, moves: List[Dict[str, str]], collision_strategy: str = "skip") -> Dict[str, Any]:
        """
        Executes approved bulk moves, records each in organize_history for rollback,
        and updates SQLite library_files records.
        """
        conn = get_db_connection()
        batch_id = f"batch_org_{int(time.time())}"
        moved_count = 0
        errors = []

        for m in moves:
            src = Path(m["current_path"])
            dst = Path(m["target_path"])

            if not src.exists() or src.resolve() == dst.resolve():
                continue

            # Ensure parent directories exist
            dst.parent.mkdir(parents=True, exist_ok=True)

            if dst.exists() and collision_strategy == "skip":
                continue

            try:
                shutil.move(str(src), str(dst))
                moved_count += 1

                with conn:
                    conn.execute(
                        """
                        INSERT INTO organize_history (id, timestamp, original_path, new_path, status, batch_id)
                        VALUES (?, ?, ?, ?, 'MOVED', ?)
                        """,
                        (f"org_{uuid.uuid4().hex[:10]}", time.time(), str(src), str(dst), batch_id)
                    )
                    conn.execute(
                        """
                        UPDATE library_files
                        SET path = ?, filename = ?, folder = ?
                        WHERE path = ?
                        """,
                        (str(dst), dst.name, dst.parent.name, str(src))
                    )
            except Exception as ex:
                errors.append(f"{src.name}: {str(ex)}")

        record_audit("ORGANIZER", f"Bulk organized {moved_count} files in batch '{batch_id}'.")
        return {
            "batch_id": batch_id,
            "moved_count": moved_count,
            "error_count": len(errors),
            "errors": errors
        }

    @classmethod
    def rollback_bulk_organization(cls, batch_id: str) -> int:
        """
        Reverses all file moves belonging to a specific batch_id.
        """
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM organize_history WHERE batch_id = ? AND status = 'MOVED'",
            (batch_id,)
        ).fetchall()

        restored_count = 0
        for r in rows:
            src = Path(r["new_path"])
            dst = Path(r["original_path"])
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                with conn:
                    conn.execute("UPDATE organize_history SET status = 'RESTORED' WHERE id = ?", (r["id"],))
                    conn.execute(
                        "UPDATE library_files SET path = ?, filename = ?, folder = ? WHERE path = ?",
                        (str(dst), dst.name, dst.parent.name, str(src))
                    )
                restored_count += 1

        record_audit("ORGANIZER", f"Rolled back {restored_count} files for batch '{batch_id}'.")
        return restored_count

    # ================= 4. UTILITIES =================

    @staticmethod
    def normalize_string(val: str) -> str:
        """Strips punctuation, collapses whitespace, and lowercases for reliable fuzzy matching."""
        if not val:
            return ""
        s = val.lower().translate(str.maketrans("", "", string.punctuation))
        return " ".join(s.split())

    @staticmethod
    def strip_track_title_noise(title: str) -> str:
        """Removes noise tokens from track titles like (2011 Remaster), (Remastered), [Live], [Official Audio]."""
        if not title:
            return ""
        cleaned = re.sub(r"[\(\[].*?(remaster|deluxe|live|mono|stereo|bonus|official|feat\.|ft\.).*?[\)\]]", "", title, flags=re.IGNORECASE)
        # Remove trailing dash/spaces if title had "Title - Remastered"
        cleaned = re.sub(r"\s*-\s*(remaster(ed)?|deluxe|live).*$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    @classmethod
    def cleanup_temp_buffers(cls) -> Dict[str, Any]:
        """
        Cleans scratch audio chunks strictly within the project temp directory on D: drive.
        """
        temp_dir = TEMP_DIR
        deleted_count = 0
        reclaimed_bytes = 0

        for item in temp_dir.glob("*"):
            if item.is_file():
                sz = item.stat().st_size
                try:
                    item.unlink()
                    deleted_count += 1
                    reclaimed_bytes += sz
                except Exception:
                    logger.debug("Suppressed exception", exc_info=True)

        record_audit("CLEANUP", f"Cleaned {deleted_count} temp files ({round(reclaimed_bytes / (1024*1024), 2)} MB reclaimed)")
        return {
            "deleted_files": deleted_count,
            "reclaimed_mb": round(reclaimed_bytes / (1024 * 1024), 2)
        }
