import datetime
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config_loader import PROJECT_ROOT
from src.db import RESTORE_PENDING, RESTORE_PENDING_INFO, RESTORE_RESULT, get_db_connection, get_db_path

BACKUPS_DIR = PROJECT_ROOT / "data" / "backups"


def _data_dir() -> Path:
    """Folder holding studio.db, where a staged restore waits for the next start."""
    return get_db_path().parent


class BackupService:
    """
    Database and configuration backup, restore, and integrity verification service.
    """

    @classmethod
    def create_database_backup(cls, reason: str = "manual") -> Path:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = BACKUPS_DIR / f"studio_{reason}_{timestamp}.db"

        # Use SQLite backup API for online non-blocking backup
        conn = get_db_connection()
        dest_conn = sqlite3.connect(str(backup_file))
        with dest_conn:
            conn.backup(dest_conn, pages=100)
        dest_conn.close()

        return backup_file

    @classmethod
    def list_backups(cls) -> List[dict]:
        if not BACKUPS_DIR.exists():
            return []
        items: List[dict] = []
        for f in BACKUPS_DIR.glob("*.db"):
            stat = f.stat()
            items.append({
                "filename": f.name,
                "path": str(f),
                "size_kb": round(stat.st_size / 1024, 1),
                "created": stat.st_mtime,
                "created_str": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            })
        items.sort(key=lambda x: x["created"], reverse=True)
        return items

    @classmethod
    def verify_database_integrity(cls) -> Tuple[bool, str]:
        conn = get_db_connection()
        cur = conn.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        status = row[0] if row else "unknown"
        return status == "ok", status

    # ---- restore (staged; applied by src.db on the next start)

    @classmethod
    def _find_backup(cls, filename: str) -> Path:
        """Only files this service lists can be restored, so a crafted name can never point elsewhere."""
        for item in cls.list_backups():
            if item["filename"] == filename:
                return Path(item["path"])
        raise ValueError("That backup does not exist")

    @classmethod
    def stage_restore(cls, filename: str) -> Dict[str, Any]:
        """Check the chosen backup and stage it; nothing changes until the app is restarted."""
        source = cls._find_backup(filename)
        probe = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            status = probe.execute("PRAGMA integrity_check").fetchone()[0]
            has_schema = probe.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        except sqlite3.DatabaseError as e:
            raise ValueError(f"That backup is damaged and cannot be restored ({e})") from e
        finally:
            probe.close()
        if status != "ok":
            raise ValueError(f"That backup is damaged and cannot be restored ({status})")
        if not has_schema:
            raise ValueError("That file is not a Spotify Downloader Studio backup")
        data_dir = _data_dir()
        tmp = data_dir / (RESTORE_PENDING + ".part")
        shutil.copy2(source, tmp)
        os.replace(tmp, data_dir / RESTORE_PENDING)           # atomic: a half-copied file is never picked up
        info = {"source": source.name, "staged_at": time.time()}
        (data_dir / RESTORE_PENDING_INFO).write_text(json.dumps(info), encoding="utf-8")
        return info

    @classmethod
    def pending_restore(cls) -> Optional[Dict[str, Any]]:
        data_dir = _data_dir()
        if not (data_dir / RESTORE_PENDING).exists():
            return None
        try:
            return json.loads((data_dir / RESTORE_PENDING_INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"source": None, "staged_at": None}

    @classmethod
    def cancel_restore(cls) -> bool:
        data_dir = _data_dir()
        existed = (data_dir / RESTORE_PENDING).exists()
        (data_dir / RESTORE_PENDING).unlink(missing_ok=True)
        (data_dir / RESTORE_PENDING_INFO).unlink(missing_ok=True)
        return existed

    @classmethod
    def last_restore(cls) -> Optional[Dict[str, Any]]:
        try:
            return json.loads((_data_dir() / RESTORE_RESULT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @classmethod
    def dismiss_last_restore(cls) -> None:
        (_data_dir() / RESTORE_RESULT).unlink(missing_ok=True)
