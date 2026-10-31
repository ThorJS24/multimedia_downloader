import datetime
import sqlite3
from pathlib import Path
from typing import List, Tuple

from src.config_loader import PROJECT_ROOT
from src.db import get_db_connection

BACKUPS_DIR = PROJECT_ROOT / "data" / "backups"


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
