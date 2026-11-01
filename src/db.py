import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config_loader import get_data_dir

logger = logging.getLogger("Database")


def get_db_path() -> Path:
    return get_data_dir() / "data" / "studio.db"


DB_PATH = get_db_path()
_local = threading.local()


class _CloseOnDrop:
    """Closes its connection when the owning thread (and so its thread-local storage) goes away."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __del__(self):
        try:
            self.conn.close()
        except Exception:
            pass


def get_db_connection() -> sqlite3.Connection:
    global DB_PATH
    current_path = get_db_path()
    DB_PATH = current_path

    if hasattr(_local, "conn") and _local.conn is not None:
        if getattr(_local, "conn_path", None) != str(current_path):
            try:
                _local.conn.close()
            except Exception:
                logger.debug("Closing the previous database connection failed (switching database file)", exc_info=True)
            _local.conn = None

    if not hasattr(_local, "conn") or _local.conn is None:
        current_path.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(current_path), timeout=30.0)
        _local.conn_path = str(current_path)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode = WAL")
        _local.conn.execute("PRAGMA synchronous = NORMAL")
        _local.conn.execute("PRAGMA busy_timeout = 30000")
        _local.conn.execute("PRAGMA foreign_keys = ON")
        _local.guard = _CloseOnDrop(_local.conn)   # no 'unclosed database' leak when a worker thread ends
    return _local.conn


def close_db_connection():
    """Explicitly closes thread-local database connection."""
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            logger.debug("Closing the database connection failed", exc_info=True)
        _local.conn = None
        _local.conn_path = None


MIGRATIONS: List[Tuple[int, str, str]] = [
    (
        1,
        "initial_schema",
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL,
            collection_id TEXT,
            collection_name TEXT,
            title TEXT,
            artists TEXT,
            album TEXT,
            duration_ms INTEGER DEFAULT 0,
            cover_url TEXT,
            isrc TEXT,
            year TEXT,
            stage TEXT DEFAULT 'QUEUED',
            status TEXT DEFAULT 'QUEUED',
            progress REAL DEFAULT 0.0,
            speed TEXT DEFAULT '0 KB/s',
            eta TEXT DEFAULT '--',
            output_path TEXT,
            error TEXT,
            match_confidence REAL DEFAULT 1.0,
            match_source TEXT DEFAULT 'Spotify AP CDN',
            added_at REAL,
            completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS collections (
            id TEXT PRIMARY KEY,
            type TEXT,
            name TEXT,
            owner TEXT,
            cover_url TEXT,
            track_count INTEGER DEFAULT 0,
            last_synced REAL
        );

        CREATE TABLE IF NOT EXISTS collection_tracks (
            collection_id TEXT,
            track_id TEXT,
            position INTEGER,
            title TEXT,
            artists TEXT,
            album TEXT,
            duration_ms INTEGER,
            cover_url TEXT,
            isrc TEXT,
            PRIMARY KEY (collection_id, track_id)
        );

        CREATE TABLE IF NOT EXISTS library_files (
            path TEXT PRIMARY KEY,
            filename TEXT,
            folder TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration_sec REAL,
            format TEXT,
            size_bytes INTEGER,
            checksum TEXT,
            last_scanned REAL
        );

        CREATE TABLE IF NOT EXISTS sync_snapshots (
            collection_id TEXT PRIMARY KEY,
            collection_name TEXT,
            track_count INTEGER,
            snapshot_json TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            level TEXT,
            category TEXT,
            message TEXT
        );

        CREATE TABLE IF NOT EXISTS fingerprints (
            path TEXT PRIMARY KEY,
            hash TEXT,
            duration_sec REAL,
            pcm_peaks TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS quality_audits (
            path TEXT PRIMARY KEY,
            codec TEXT,
            bitrate_kbps INTEGER,
            sample_rate INTEGER,
            channels INTEGER,
            bit_depth INTEGER,
            lufs_integrated REAL,
            true_peak_dbtp REAL,
            silence_start_sec REAL,
            silence_end_sec REAL,
            status TEXT,
            checked_at REAL
        );

        CREATE TABLE IF NOT EXISTS monitored_artists (
            id TEXT PRIMARY KEY,
            name TEXT,
            spotify_id TEXT,
            last_checked REAL,
            auto_download INTEGER DEFAULT 0,
            folder_override TEXT
        );

        CREATE TABLE IF NOT EXISTS monitored_playlists (
            id TEXT PRIMARY KEY,
            name TEXT,
            spotify_url TEXT,
            last_track_count INTEGER DEFAULT 0,
            last_checked REAL,
            auto_download INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS release_calendar (
            id TEXT PRIMARY KEY,
            artist_id TEXT,
            artist_name TEXT,
            title TEXT,
            release_type TEXT,
            release_date TEXT,
            cover_url TEXT,
            spotify_url TEXT,
            status TEXT DEFAULT 'UPCOMING',
            detected_at REAL
        );

        CREATE TABLE IF NOT EXISTS download_archive (
            resource_id TEXT PRIMARY KEY,
            resource_type TEXT,
            title TEXT,
            artist TEXT,
            file_path TEXT,
            downloaded_at REAL
        );

        CREATE TABLE IF NOT EXISTS metadata_provenance (
            track_id TEXT,
            field_name TEXT,
            value TEXT,
            source TEXT,
            confidence REAL,
            updated_at REAL,
            PRIMARY KEY (track_id, field_name)
        );

        CREATE TABLE IF NOT EXISTS plugin_hooks (
            id TEXT PRIMARY KEY,
            name TEXT,
            event_type TEXT,
            action_type TEXT,
            target TEXT,
            is_enabled INTEGER DEFAULT 1,
            config_json TEXT
        );
        """
    ),
    (
        2,
        "performance_indexes",
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
        CREATE INDEX IF NOT EXISTS idx_jobs_track_id ON jobs (track_id);
        CREATE INDEX IF NOT EXISTS idx_library_checksum ON library_files (checksum);
        CREATE INDEX IF NOT EXISTS idx_collection_tracks_cid ON collection_tracks (collection_id);
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs (timestamp);
        """
    ),
    (
        3,
        "staging_workflow_table",
        """
        CREATE TABLE IF NOT EXISTS staging_items (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            title TEXT,
            artists TEXT,
            album TEXT,
            duration_ms INTEGER,
            target_path TEXT,
            collision_status TEXT,
            status TEXT DEFAULT 'STAGED',
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_staging_batch_id ON staging_items (batch_id);
        """
    ),
    (
        4,
        "library_intelligence_schema",
        """
        ALTER TABLE library_files ADD COLUMN mtime REAL DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN sha256 TEXT;
        ALTER TABLE library_files ADD COLUMN album_artist TEXT;
        ALTER TABLE library_files ADD COLUMN track_number INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN total_tracks INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN disc_number INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN year TEXT;
        ALTER TABLE library_files ADD COLUMN genre TEXT;
        ALTER TABLE library_files ADD COLUMN has_artwork INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN codec TEXT;
        ALTER TABLE library_files ADD COLUMN bitrate_kbps INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN sample_rate INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN channels INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN bit_depth INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN tag_status TEXT DEFAULT 'VALID';
        ALTER TABLE library_files ADD COLUMN issues_json TEXT;

        CREATE INDEX IF NOT EXISTS idx_library_sha256 ON library_files (sha256);
        CREATE INDEX IF NOT EXISTS idx_library_tag_status ON library_files (tag_status);

        CREATE TABLE IF NOT EXISTS library_scans (
            id TEXT PRIMARY KEY,
            started_at REAL,
            completed_at REAL,
            status TEXT,
            scanned_files INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            errors_json TEXT
        );

        CREATE TABLE IF NOT EXISTS organize_history (
            id TEXT PRIMARY KEY,
            timestamp REAL,
            original_path TEXT,
            new_path TEXT,
            status TEXT,
            batch_id TEXT
        );
        """
    ),
    (
        5,
        "folder_watcher_schema",
        """
        CREATE TABLE IF NOT EXISTS watched_folders (
            id TEXT PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            label TEXT,
            recursive INTEGER DEFAULT 1,
            is_enabled INTEGER DEFAULT 1,
            added_at REAL,
            last_scanned_at REAL,
            status TEXT DEFAULT 'ACTIVE',
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS watcher_events (
            id TEXT PRIMARY KEY,
            folder_id TEXT,
            file_path TEXT NOT NULL,
            event_type TEXT NOT NULL,
            file_size INTEGER,
            mtime REAL,
            content_hash TEXT,
            detected_at REAL,
            status TEXT DEFAULT 'PENDING',
            staging_item_id TEXT,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_watcher_folder_id ON watcher_events (folder_id);
        CREATE INDEX IF NOT EXISTS idx_watcher_file_path ON watcher_events (file_path);
        CREATE INDEX IF NOT EXISTS idx_watcher_status ON watcher_events (status);

        ALTER TABLE staging_items ADD COLUMN source_type TEXT DEFAULT 'DOWNLOAD';
        ALTER TABLE staging_items ADD COLUMN source_path TEXT;
        ALTER TABLE staging_items ADD COLUMN detection_reason TEXT;
        """
    ),
    (
        6,
        "rules_engine_and_indexes",
        """
        CREATE TABLE IF NOT EXISTS organization_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            priority INTEGER DEFAULT 10,
            condition_type TEXT NOT NULL,
            condition_value TEXT NOT NULL,
            action_type TEXT NOT NULL,
            action_value TEXT NOT NULL,
            is_enabled INTEGER DEFAULT 1,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS rules_execution_log (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            rule_id TEXT,
            source_path TEXT NOT NULL,
            target_path TEXT NOT NULL,
            action_taken TEXT NOT NULL,
            status TEXT DEFAULT 'SUCCESS',
            details TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_rules_priority ON organization_rules (priority);
        CREATE INDEX IF NOT EXISTS idx_rules_log_batch ON rules_execution_log (batch_id);
        CREATE INDEX IF NOT EXISTS idx_lib_artist_album ON library_files (artist, album);
        CREATE INDEX IF NOT EXISTS idx_lib_title ON library_files (title);
        """
    ),
    (
        7,
        "playback_tracking_and_indexes",
        """
        ALTER TABLE library_files ADD COLUMN play_count INTEGER DEFAULT 0;
        ALTER TABLE library_files ADD COLUMN last_played_at REAL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_library_artist ON library_files (artist);
        CREATE INDEX IF NOT EXISTS idx_library_album ON library_files (album);
        CREATE INDEX IF NOT EXISTS idx_library_last_played ON library_files (last_played_at);
        """
    ),
    (
        8,
        "phase29_multi_source_schema",
        """
        ALTER TABLE jobs ADD COLUMN provider TEXT DEFAULT 'spotify';
        ALTER TABLE jobs ADD COLUMN provider_item_id TEXT;
        ALTER TABLE jobs ADD COLUMN canonical_url TEXT;
        ALTER TABLE jobs ADD COLUMN is_alternative INTEGER DEFAULT 0;
        ALTER TABLE jobs ADD COLUMN original_spotify_id TEXT;
        ALTER TABLE jobs ADD COLUMN capabilities_json TEXT;

        ALTER TABLE collection_tracks ADD COLUMN provider TEXT DEFAULT 'spotify';
        ALTER TABLE collection_tracks ADD COLUMN provider_item_id TEXT;
        ALTER TABLE collection_tracks ADD COLUMN canonical_url TEXT;
        ALTER TABLE collection_tracks ADD COLUMN is_alternative INTEGER DEFAULT 0;
        ALTER TABLE collection_tracks ADD COLUMN original_spotify_id TEXT;
        ALTER TABLE collection_tracks ADD COLUMN capabilities_json TEXT;

        ALTER TABLE collections ADD COLUMN provider TEXT DEFAULT 'spotify';
        ALTER TABLE collections ADD COLUMN canonical_url TEXT;
        ALTER TABLE collections ADD COLUMN is_mixed_source INTEGER DEFAULT 0;
        ALTER TABLE collections ADD COLUMN providers_json TEXT;

        CREATE TABLE IF NOT EXISTS cross_source_matches (
            id TEXT PRIMARY KEY,
            source_provider TEXT NOT NULL,
            source_item_id TEXT NOT NULL,
            target_provider TEXT NOT NULL,
            target_item_id TEXT NOT NULL,
            match_status TEXT NOT NULL,
            confidence_score REAL DEFAULT 0.0,
            rationale TEXT,
            evidence_json TEXT,
            is_user_selected INTEGER DEFAULT 0,
            created_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_provider ON jobs (provider);
        CREATE INDEX IF NOT EXISTS idx_cross_source_match ON cross_source_matches (source_provider, source_item_id);
        """
    ),
    (
        9,
        "retries_and_retention",
        """
        ALTER TABLE jobs ADD COLUMN attempts INTEGER DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_jobs_added_at ON jobs (added_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_completed_at ON jobs (completed_at);
        CREATE INDEX IF NOT EXISTS idx_release_calendar_detected ON release_calendar (detected_at);
        """
    ),
    (
        10,
        "spectral_fingerprints",
        """
        -- fingerprints are a cache: v1 hashed time-domain amplitude, not frequency content
        DELETE FROM fingerprints;
        ALTER TABLE fingerprints ADD COLUMN tokens TEXT;
        """
    ),
    (
        11,
        "foreign_keys_for_real_parent_child_tables",
        """
        -- Rebuild the two child tables with enforced foreign keys. Orphans left behind by earlier
        -- versions (e.g. release rows of an artist that is no longer monitored) are dropped.
        CREATE TABLE watcher_events_new (
            id TEXT PRIMARY KEY,
            folder_id TEXT REFERENCES watched_folders(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            event_type TEXT NOT NULL,
            file_size INTEGER,
            mtime REAL,
            content_hash TEXT,
            detected_at REAL,
            status TEXT DEFAULT 'PENDING',
            staging_item_id TEXT,
            error TEXT
        );
        INSERT INTO watcher_events_new
            SELECT * FROM watcher_events WHERE folder_id IS NULL OR folder_id IN (SELECT id FROM watched_folders);
        DROP TABLE watcher_events;
        ALTER TABLE watcher_events_new RENAME TO watcher_events;
        CREATE INDEX IF NOT EXISTS idx_watcher_folder_id ON watcher_events (folder_id);
        CREATE INDEX IF NOT EXISTS idx_watcher_file_path ON watcher_events (file_path);
        CREATE INDEX IF NOT EXISTS idx_watcher_status ON watcher_events (status);

        CREATE TABLE release_calendar_new (
            id TEXT PRIMARY KEY,
            artist_id TEXT REFERENCES monitored_artists(id) ON DELETE CASCADE,
            artist_name TEXT,
            title TEXT,
            release_type TEXT,
            release_date TEXT,
            cover_url TEXT,
            spotify_url TEXT,
            status TEXT DEFAULT 'UPCOMING',
            detected_at REAL
        );
        INSERT INTO release_calendar_new
            SELECT * FROM release_calendar WHERE artist_id IS NULL OR artist_id IN (SELECT id FROM monitored_artists);
        DROP TABLE release_calendar;
        ALTER TABLE release_calendar_new RENAME TO release_calendar;
        CREATE INDEX IF NOT EXISTS idx_release_calendar_detected ON release_calendar (detected_at);
        """
    ),
    (
        12,
        "plugin_packs",
        """
        CREATE TABLE IF NOT EXISTS plugin_packs (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'bundled',
            enabled INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL DEFAULT '{}',
            manifest_json TEXT,
            installed_at REAL,
            last_status_json TEXT
        );
        """
    ),
]


def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at REAL NOT NULL
        )
        """)

        # Discover currently applied versions
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for version, name, sql in MIGRATIONS:
            if version not in applied:
                logger.info(f"Applying schema migration {version}: {name}")
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, time.time())
                )


def get_migration_version() -> int:
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT MAX(version) as v FROM schema_migrations").fetchone()
        return row["v"] if row and row["v"] is not None else 0
    except Exception:
        return 0


def recover_stale_jobs() -> int:
    """
    Recovers any jobs left in an unfinished or 'ACTIVE' state after a crash or shutdown.
    Re-queues them cleanly to prevent permanently stuck states.
    """
    try:
        conn = get_db_connection()
        with conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'QUEUED', stage = 'QUEUED', speed = '0 KB/s', progress = 0.0
                WHERE status = 'ACTIVE' OR (status NOT IN ('COMPLETED', 'SKIPPED', 'CANCELLED', 'QUEUED') AND completed_at IS NULL)
                """
            )
            count = cur.rowcount
            if count > 0:
                logger.info(f"Crash recovery: Re-queued {count} stale active jobs.")
            return count
    except Exception as e:
        logger.warning(f"Failed to recover stale jobs: {e}")
        return 0


# A restore is staged next to the database and swapped in on the next start, before any connection exists
# (replacing a live WAL database underneath running threads would corrupt it).
RESTORE_PENDING = "restore_pending.db"
RESTORE_PENDING_INFO = "restore_pending.json"
RESTORE_RESULT = "restore_result.json"


def apply_pending_restore(db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Swap a staged backup in as the live database. The database being replaced is saved first, so a restore
    can itself be undone. Returns what happened (also written to restore_result.json), or None when nothing was staged."""
    db_path = db_path or get_db_path()
    pending = db_path.parent / RESTORE_PENDING
    info_path = db_path.parent / RESTORE_PENDING_INFO
    if not pending.exists():
        return None
    result: Dict[str, Any] = {"at": time.time(), "ok": False, "source": None, "saved_previous_as": None, "error": None}
    try:
        if info_path.exists():
            result["source"] = json.loads(info_path.read_text(encoding="utf-8")).get("source")
        probe = sqlite3.connect(f"file:{pending.as_posix()}?mode=ro", uri=True)
        try:
            status = probe.execute("PRAGMA integrity_check").fetchone()[0]
            has_schema = probe.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        finally:
            probe.close()
        if status != "ok" or not has_schema:
            raise ValueError(f"The staged backup failed its integrity check ({status if status != 'ok' else 'not a Studio database'})")

        if db_path.exists():
            backups = db_path.parent / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            saved = backups / f"studio_prerestore_{time.strftime('%Y%m%d_%H%M%S')}.db"
            src_conn = sqlite3.connect(str(db_path))           # the backup API also captures what is still in the WAL
            try:
                dest_conn = sqlite3.connect(str(saved))
                try:
                    src_conn.backup(dest_conn)
                finally:
                    dest_conn.close()
            finally:
                src_conn.close()
            result["saved_previous_as"] = saved.name
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        os.replace(pending, db_path)
        result["ok"] = True
        logger.info("Restored the database from %s", result["source"])
    except Exception as e:
        result["error"] = str(e)
        logger.error("Staged database restore was not applied: %s", e, exc_info=True)
        try:
            pending.replace(pending.with_name("restore_failed.db"))
        except OSError:
            logger.warning("Could not set the failed staged restore aside", exc_info=True)
    finally:
        info_path.unlink(missing_ok=True)
        try:
            (db_path.parent / RESTORE_RESULT).write_text(json.dumps(result), encoding="utf-8")
        except OSError:
            logger.warning("Could not record the restore result", exc_info=True)
    return result


# Call on import to guarantee schema presence and recovery
apply_pending_restore()
init_db()
recover_stale_jobs()


def record_audit(category: str, message: str, level: str = "INFO"):
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO audit_logs (timestamp, level, category, message) VALUES (?, ?, ?, ?)",
                (time.time(), level, category, message)
            )
    except Exception as exc:
        logger.debug("Could not write audit record: %s", exc)
