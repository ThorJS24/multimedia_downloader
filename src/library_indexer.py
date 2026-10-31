import csv
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import mutagen

from src.db import get_db_connection, record_audit

logger = logging.getLogger("LibraryIndexer")


class LibraryIndexer:
    """
    Music library indexer, Tag Inspector, and Technical Auditor.
    Scans local audio files, extracts comprehensive metadata and technical streams,
    detects anomalies and missing tags, calculates cryptographic SHA-256 hashes,
    and supports background cancellable scanning with cached incremental updates.
    """

    SUPPORTED_EXTS = {".mp3", ".ogg", ".flac", ".m4a", ".mp4", ".wav", ".opus", ".aac"}

    # Scanning concurrency and control state
    _scan_lock = threading.Lock()
    _is_scanning = False
    _cancel_requested = False
    _pause_event = threading.Event()
    _pause_event.set()  # Set = running, Clear = paused
    _scan_progress: Dict[str, Any] = {
        "scan_id": None,
        "status": "IDLE",  # IDLE, RUNNING, PAUSED, CANCELLED, COMPLETED, FAILED
        "current": 0,
        "total": 0,
        "current_file": "",
        "errors": [],
        "started_at": 0,
        "elapsed_sec": 0
    }

    @classmethod
    def get_scan_status(cls) -> Dict[str, Any]:
        with cls._scan_lock:
            status_copy = dict(cls._scan_progress)
            if status_copy["started_at"] and status_copy["status"] in ("RUNNING", "PAUSED"):
                status_copy["elapsed_sec"] = round(time.time() - status_copy["started_at"], 1)
            return status_copy

    @classmethod
    def pause_scan(cls) -> bool:
        with cls._scan_lock:
            if cls._is_scanning:
                cls._pause_event.clear()
                cls._scan_progress["status"] = "PAUSED"
                return True
            return False

    @classmethod
    def resume_scan(cls) -> bool:
        with cls._scan_lock:
            if cls._is_scanning:
                cls._pause_event.set()
                cls._scan_progress["status"] = "RUNNING"
                return True
            return False

    @classmethod
    def cancel_scan(cls) -> bool:
        with cls._scan_lock:
            if cls._is_scanning:
                cls._cancel_requested = True
                cls._pause_event.set()  # Unpause to allow loop to exit
                cls._scan_progress["status"] = "CANCELLED"
                return True
            return False

    @classmethod
    def start_background_scan(cls, dir_path: Path) -> str:
        with cls._scan_lock:
            if cls._is_scanning:
                return cls._scan_progress["scan_id"] or "active_scan"

            scan_id = f"scan_{uuid.uuid4().hex[:10]}"
            cls._is_scanning = True
            cls._cancel_requested = False
            cls._pause_event.set()
            cls._scan_progress = {
                "scan_id": scan_id,
                "status": "RUNNING",
                "current": 0,
                "total": 0,
                "current_file": "Discovering files...",
                "errors": [],
                "started_at": time.time(),
                "elapsed_sec": 0
            }

        worker = threading.Thread(
            target=cls._run_scan_worker,
            args=(dir_path, scan_id),
            name=f"IndexerWorker-{scan_id}",
            daemon=True
        )
        worker.start()
        return scan_id

    @classmethod
    def _run_scan_worker(cls, dir_path: Path, scan_id: str):
        conn = get_db_connection()
        started_at = time.time()
        errors: List[str] = []
        scanned_count = 0

        # Create record in library_scans
        with conn:
            conn.execute(
                """
                INSERT INTO library_scans (id, started_at, status, scanned_files, total_files, error_count, errors_json)
                VALUES (?, ?, 'RUNNING', 0, 0, 0, '[]')
                """,
                (scan_id, started_at)
            )

        try:
            if not dir_path.exists():
                raise FileNotFoundError(f"Target directory '{dir_path}' does not exist.")

            # Phase 1: File discovery
            all_files: List[Path] = []
            for root, _, files in os.walk(dir_path):
                if cls._cancel_requested:
                    break
                for f in files:
                    p = Path(root) / f
                    if p.suffix.lower() in cls.SUPPORTED_EXTS:
                        all_files.append(p)

            total_files = len(all_files)
            with cls._scan_lock:
                cls._scan_progress["total"] = total_files

            with conn:
                conn.execute("UPDATE library_scans SET total_files = ? WHERE id = ?", (total_files, scan_id))

            # Phase 2: Index each file
            for idx, file_path in enumerate(all_files, start=1):
                if cls._cancel_requested:
                    logger.info(f"Scan {scan_id} cancelled by user.")
                    break

                cls._pause_event.wait()

                with cls._scan_lock:
                    cls._scan_progress["current"] = idx
                    cls._scan_progress["current_file"] = file_path.name

                try:
                    cls._index_file(conn, file_path)
                    scanned_count += 1
                except Exception as ex:
                    err_msg = f"{file_path.name}: {str(ex)}"
                    errors.append(err_msg)
                    logger.warning(f"Error indexing {file_path}: {ex}")

            final_status = "CANCELLED" if cls._cancel_requested else "COMPLETED"
        except Exception as e:
            final_status = "FAILED"
            errors.append(f"Fatal scan error: {str(e)}")
            logger.error(f"Fatal library scan failure: {e}")
        finally:
            completed_at = time.time()
            with conn:
                conn.execute(
                    """
                    UPDATE library_scans
                    SET completed_at = ?, status = ?, scanned_files = ?, error_count = ?, errors_json = ?
                    WHERE id = ?
                    """,
                    (completed_at, final_status, scanned_count, len(errors), json.dumps(errors[:100]), scan_id)
                )

            with cls._scan_lock:
                cls._is_scanning = False
                cls._scan_progress["status"] = final_status
                cls._scan_progress["errors"] = errors
                cls._scan_progress["elapsed_sec"] = round(completed_at - started_at, 1)

            record_audit("LIBRARY", f"Scan {scan_id} {final_status.lower()}: {scanned_count}/{len(all_files if 'all_files' in locals() else [])} files indexed.")

    @classmethod
    def scan_directory(cls, dir_path: Path) -> int:
        """Synchronous scan for testing and initialization."""
        if not dir_path.exists():
            return 0

        conn = get_db_connection()
        scanned_count = 0
        for root, _, files in os.walk(dir_path):
            for file in files:
                p = Path(root) / file
                if p.suffix.lower() in cls.SUPPORTED_EXTS:
                    cls._index_file(conn, p)
                    scanned_count += 1
        return scanned_count

    @classmethod
    def index_single_file(cls, file_path: Path) -> bool:
        """
        Public entrypoint to index a single local audio file into the database.
        """
        try:
            if not file_path.exists() or file_path.suffix.lower() not in cls.SUPPORTED_EXTS:
                return False
            conn = get_db_connection()
            return cls._index_file(conn, file_path)
        except Exception as e:
            logger.error(f"Error indexing single file {file_path}: {e}")
            return False

    @classmethod
    def _index_file(cls, conn, file_path: Path) -> bool:
        """
        Parses tags, technical stream details, checks caching via mtime,
        and persists record to library_files.
        """
        try:
            stat = file_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            return False  # File inaccessible

        # Check incremental cache: If mtime, size, and sha256 already recorded, skip expensive re-read
        cur = conn.execute(
            "SELECT mtime, size_bytes, sha256 FROM library_files WHERE path = ?",
            (str(file_path),)
        )
        cached = cur.fetchone()
        if cached and cached["mtime"] == mtime and cached["size_bytes"] == size and cached["sha256"]:
            with conn:
                conn.execute("UPDATE library_files SET last_scanned = ? WHERE path = ?", (time.time(), str(file_path)))
            return True

        # 1. Cryptographic full content SHA-256
        sha256_hash = cls.compute_file_sha256(file_path)

        # 2. Extract Mutagen tags and stream technicals
        meta, tech, tag_status, issues = cls.inspect_file_metadata(file_path)

        with conn:
            conn.execute(
                """
                INSERT INTO library_files (
                    path, filename, folder, title, artist, album, duration_sec, format, size_bytes,
                    checksum, last_scanned, mtime, sha256, album_artist, track_number, total_tracks,
                    disc_number, year, genre, has_artwork, codec, bitrate_kbps, sample_rate,
                    channels, bit_depth, tag_status, issues_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    filename = excluded.filename,
                    folder = excluded.folder,
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    duration_sec = excluded.duration_sec,
                    format = excluded.format,
                    size_bytes = excluded.size_bytes,
                    checksum = excluded.checksum,
                    last_scanned = excluded.last_scanned,
                    mtime = excluded.mtime,
                    sha256 = excluded.sha256,
                    album_artist = excluded.album_artist,
                    track_number = excluded.track_number,
                    total_tracks = excluded.total_tracks,
                    disc_number = excluded.disc_number,
                    year = excluded.year,
                    genre = excluded.genre,
                    has_artwork = excluded.has_artwork,
                    codec = excluded.codec,
                    bitrate_kbps = excluded.bitrate_kbps,
                    sample_rate = excluded.sample_rate,
                    channels = excluded.channels,
                    bit_depth = excluded.bit_depth,
                    tag_status = excluded.tag_status,
                    issues_json = excluded.issues_json
                """,
                (
                    str(file_path),
                    file_path.name,
                    file_path.parent.name,
                    meta.get("title", file_path.stem),
                    meta.get("artist", "Unknown Artist"),
                    meta.get("album", "Unknown Album"),
                    tech.get("duration_sec", 0.0),
                    file_path.suffix.lstrip(".").lower(),
                    size,
                    sha256_hash[:16],  # short checksum alias
                    time.time(),
                    mtime,
                    sha256_hash,
                    meta.get("album_artist", ""),
                    meta.get("track_number", 0),
                    meta.get("total_tracks", 0),
                    meta.get("disc_number", 0),
                    meta.get("year", ""),
                    meta.get("genre", ""),
                    1 if meta.get("has_artwork") else 0,
                    tech.get("codec", file_path.suffix.lstrip(".")),
                    tech.get("bitrate_kbps", 0),
                    tech.get("sample_rate", 44100),
                    tech.get("channels", 2),
                    tech.get("bit_depth", 16),
                    tag_status,
                    json.dumps(issues)
                )
            )
        return True

    @staticmethod
    def compute_file_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    @classmethod
    def inspect_file_metadata(cls, file_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any], str, List[str]]:
        """
        Extracts tags, stream info, checks artwork presence, tag status, and flags anomalies.
        Returns: (metadata_dict, technical_dict, tag_status, issues_list)
        """
        meta: Dict[str, Any] = {
            "title": file_path.stem,
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "album_artist": "",
            "track_number": 0,
            "total_tracks": 0,
            "disc_number": 0,
            "year": "",
            "genre": "",
            "has_artwork": False
        }
        tech: Dict[str, Any] = {
            "duration_sec": 0.0,
            "codec": file_path.suffix.lstrip(".").lower(),
            "bitrate_kbps": 0,
            "sample_rate": 44100,
            "channels": 2,
            "bit_depth": 16
        }
        issues: List[str] = []
        tag_status = "VALID"

        try:
            audio = mutagen.File(file_path)
            if audio is None:
                tag_status = "UNREADABLE"
                issues.append("Unreadable audio container or unsupported tag format")
            else:
                # Stream technicals
                info = getattr(audio, "info", None)
                if info:
                    tech["duration_sec"] = round(float(getattr(info, "length", 0.0)), 2)
                    raw_bitrate = getattr(info, "bitrate", 0)
                    if raw_bitrate:
                        tech["bitrate_kbps"] = round(raw_bitrate / 1000)
                    tech["sample_rate"] = getattr(info, "sample_rate", 44100)
                    tech["channels"] = getattr(info, "channels", 2)
                    tech["bit_depth"] = getattr(info, "bits_per_sample", 16)

                # Mutagen tag parsing
                ext = file_path.suffix.lower()
                if ext in (".mp3", ".wav"):
                    cls._parse_id3_tags(audio, meta)
                elif ext == ".flac":
                    cls._parse_flac_tags(audio, meta)
                elif ext in (".m4a", ".mp4", ".aac"):
                    cls._parse_mp4_tags(audio, meta)
                elif ext in (".ogg", ".opus"):
                    cls._parse_ogg_tags(audio, meta)

                # Local artwork fallback
                if not meta["has_artwork"]:
                    for img_name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png"):
                        if (file_path.parent / img_name).exists():
                            meta["has_artwork"] = True
                            break

        except Exception as ex:
            tag_status = "CORRUPT"
            issues.append(f"Tag read error: {str(ex)}")

        # Validate Issues & Status
        if not meta.get("title") or meta["title"] == "Unknown Artist" or meta["title"] == file_path.stem:
            issues.append("Missing or unverified title")
        if not meta.get("artist") or meta["artist"] == "Unknown Artist":
            issues.append("Missing artist")
        if not meta.get("album") or meta["album"] == "Unknown Album":
            issues.append("Missing album")
        if not meta.get("has_artwork"):
            issues.append("Missing cover artwork")

        # Inconsistent whitespace or casing
        raw_t = meta.get("title", "")
        if raw_t != raw_t.strip():
            issues.append("Title has leading/trailing whitespace")
        raw_a = meta.get("artist", "")
        if raw_a != raw_a.strip():
            issues.append("Artist has leading/trailing whitespace")

        # Technical anomalies
        dur = tech.get("duration_sec", 0.0)
        if dur > 0 and dur < 30.0:
            issues.append(f"Unusually short duration ({dur:.1f}s)")
        elif dur > 1200.0:
            issues.append(f"Very long audio duration ({round(dur / 60, 1)}m)")

        if tech.get("bitrate_kbps", 0) > 0 and tech["bitrate_kbps"] < 128:
            issues.append(f"Low audio bitrate ({tech['bitrate_kbps']} kbps)")

        if tag_status == "VALID" and any("Missing" in iss for iss in issues):
            tag_status = "INCOMPLETE"

        return meta, tech, tag_status, issues

    @staticmethod
    def _parse_id3_tags(audio, meta: dict):
        if not hasattr(audio, "tags") or audio.tags is None:
            return
        tags = audio.tags
        if "TIT2" in tags: meta["title"] = str(tags["TIT2"])
        if "TPE1" in tags: meta["artist"] = str(tags["TPE1"])
        if "TPE2" in tags: meta["album_artist"] = str(tags["TPE2"])
        if "TALB" in tags: meta["album"] = str(tags["TALB"])
        if "TDRC" in tags: meta["year"] = str(tags["TDRC"])
        if "TCON" in tags: meta["genre"] = str(tags["TCON"])

        if "TRCK" in tags:
            raw_trck = str(tags["TRCK"])
            if "/" in raw_trck:
                p = raw_trck.split("/")
                meta["track_number"] = int(p[0]) if p[0].isdigit() else 0
                meta["total_tracks"] = int(p[1]) if p[1].isdigit() else 0
            elif raw_trck.isdigit():
                meta["track_number"] = int(raw_trck)

        if "TPOS" in tags:
            raw_tpos = str(tags["TPOS"])
            if "/" in raw_tpos:
                meta["disc_number"] = int(raw_tpos.split("/")[0]) if raw_tpos.split("/")[0].isdigit() else 0
            elif raw_tpos.isdigit():
                meta["disc_number"] = int(raw_tpos)

        # Embedded APIC artwork
        for k in tags.keys():
            if k.startswith("APIC"):
                meta["has_artwork"] = True
                break

    @staticmethod
    def _parse_flac_tags(audio, meta: dict):
        for k, v in audio.items():
            k_low = k.lower()
            if k_low == "title" and v: meta["title"] = str(v[0])
            elif k_low == "artist" and v: meta["artist"] = str(v[0])
            elif k_low == "albumartist" and v: meta["album_artist"] = str(v[0])
            elif k_low == "album" and v: meta["album"] = str(v[0])
            elif k_low == "date" and v: meta["year"] = str(v[0])
            elif k_low == "genre" and v: meta["genre"] = str(v[0])
            elif k_low == "tracknumber" and v and str(v[0]).isdigit(): meta["track_number"] = int(v[0])
            elif k_low == "discnumber" and v and str(v[0]).isdigit(): meta["disc_number"] = int(v[0])

        if hasattr(audio, "pictures") and len(audio.pictures) > 0:
            meta["has_artwork"] = True

    @staticmethod
    def _parse_mp4_tags(audio, meta: dict):
        tags = getattr(audio, "tags", {}) or {}
        if "\xa9nam" in tags: meta["title"] = str(tags["\xa9nam"][0])
        if "\xa9ART" in tags: meta["artist"] = str(tags["\xa9ART"][0])
        if "aART" in tags: meta["album_artist"] = str(tags["aART"][0])
        if "\xa9alb" in tags: meta["album"] = str(tags["\xa9alb"][0])
        if "\xa9day" in tags: meta["year"] = str(tags["\xa9day"][0])
        if "\xa9gen" in tags: meta["genre"] = str(tags["\xa9gen"][0])
        if "trkn" in tags and isinstance(tags["trkn"], list) and tags["trkn"]:
            trkn = tags["trkn"][0]
            if isinstance(trkn, tuple):
                meta["track_number"] = trkn[0]
                meta["total_tracks"] = trkn[1]
        if "disk" in tags and isinstance(tags["disk"], list) and tags["disk"]:
            disk = tags["disk"][0]
            if isinstance(disk, tuple):
                meta["disc_number"] = disk[0]
        if "covr" in tags and tags["covr"]:
            meta["has_artwork"] = True

    @staticmethod
    def _parse_ogg_tags(audio, meta: dict):
        for k, v in audio.items():
            k_low = k.lower()
            if k_low == "title" and v: meta["title"] = str(v[0])
            elif k_low == "artist" and v: meta["artist"] = str(v[0])
            elif k_low == "album" and v: meta["album"] = str(v[0])
            elif k_low == "albumartist" and v: meta["album_artist"] = str(v[0])
            elif k_low == "date" and v: meta["year"] = str(v[0])
            elif k_low == "genre" and v: meta["genre"] = str(v[0])
            elif k_low == "tracknumber" and v and str(v[0]).isdigit(): meta["track_number"] = int(v[0])
            elif k_low == "metadata_block_picture": meta["has_artwork"] = True

    @classmethod
    def get_library(
        cls,
        search: str = "",
        filter_type: str = "all",
        sort_by: str = "last_scanned",
        sort_dir: str = "desc",
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Retrieves paginated, filtered library records with drill-down views.
        """
        conn = get_db_connection()
        where_clauses = []
        params = []

        if search:
            where_clauses.append("(title LIKE ? OR artist LIKE ? OR album LIKE ? OR filename LIKE ?)")
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param, s_param])

        if filter_type == "missing_tags":
            where_clauses.append("(tag_status = 'INCOMPLETE' OR artist = 'Unknown Artist' OR album = 'Unknown Album')")
        elif filter_type == "missing_art":
            where_clauses.append("has_artwork = 0")
        elif filter_type == "unreadable":
            where_clauses.append("tag_status IN ('UNREADABLE', 'CORRUPT')")
        elif filter_type == "anomalies":
            where_clauses.append("(duration_sec < 30.0 OR duration_sec > 1200.0 OR bitrate_kbps < 128)")
        elif filter_type == "exact_duplicates":
            where_clauses.append("sha256 IN (SELECT sha256 FROM library_files WHERE sha256 IS NOT NULL AND sha256 != '' GROUP BY sha256 HAVING COUNT(*) > 1)")
        elif filter_type == "review":
            where_clauses.append("(tag_status != 'VALID' OR has_artwork = 0 OR duration_sec < 30.0)")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Validate sort column to avoid SQL injection
        allowed_sorts = {
            "title": "title",
            "artist": "artist",
            "album": "album",
            "duration": "duration_sec",
            "size": "size_bytes",
            "format": "format",
            "bitrate": "bitrate_kbps",
            "year": "year",
            "genre": "genre",
            "last_scanned": "last_scanned"
        }
        col = allowed_sorts.get(sort_by, "last_scanned")
        direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

        # Total count query
        count_cur = conn.execute(f"SELECT COUNT(*) as total FROM library_files {where_sql}", tuple(params))
        total = count_cur.fetchone()["total"]

        # Page query
        page_cur = conn.execute(
            f"SELECT * FROM library_files {where_sql} ORDER BY {col} {direction} LIMIT ? OFFSET ?",
            tuple(params + [limit, offset])
        )
        rows = [dict(r) for r in page_cur.fetchall()]
        for r in rows:
            if r.get("issues_json"):
                try:
                    r["issues"] = json.loads(r["issues_json"])
                except Exception:
                    r["issues"] = []
            else:
                r["issues"] = []

        return {
            "files": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filter": filter_type
        }

    @classmethod
    def export_m3u(cls, output_m3u_path: Path, files: List[dict]):
        with open(output_m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for item in files:
                # NULLs are normal for files that were never fully analysed; tags may also contain
                # newlines, which would otherwise inject extra lines into the playlist.
                dur = int(item.get("duration_sec") or 0)
                artist = " ".join(str(item.get("artist") or "Unknown").split())
                title = " ".join(str(item.get("title") or item.get("filename") or "Unknown").split())
                f.write(f"#EXTINF:{dur},{artist} - {title}\n")
                f.write(f"{item['path']}\n")

    @classmethod
    def export_csv(cls, output_csv_path: Path, files: List[dict]):
        if not files:
            return
        keys = ["filename", "title", "artist", "album", "duration_sec", "format", "size_bytes", "path"]
        with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(files)

    @classmethod
    def export_audit_csv(cls, output_csv_path: Path) -> int:
        """
        Exports full technical quality & metadata audit of all library files to CSV.
        """
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM library_files ORDER BY artist ASC, title ASC").fetchall()
        files = [dict(r) for r in rows]
        if not files:
            return 0

        keys = [
            "filename", "title", "artist", "album", "album_artist", "track_number", "year",
            "format", "codec", "bitrate_kbps", "sample_rate", "channels", "duration_sec",
            "size_bytes", "has_artwork", "tag_status", "sha256", "issues_json", "path"
        ]
        with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(files)
        return len(files)
