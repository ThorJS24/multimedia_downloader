"""
Background housekeeping: record retention, scheduled database backups, and the artist-release
monitor. Started once by the application shell (desktop window or headless server).
"""
import logging
import threading
import time
from typing import Dict, Optional

from src.backup_service import BackupService
from src.db import get_db_connection, record_audit
from src.scheduler_service import SchedulerService
from src.settings_service import SettingsService

logger = logging.getLogger("MaintenanceService")

TERMINAL_STATUSES = ("COMPLETED", "SKIPPED", "CANCELLED", "FAILED")
TICK_SECONDS = 300
KEEP_AUTO_BACKUPS = 7
DAY = 86400.0


class MaintenanceService:
    _thread: Optional[threading.Thread] = None
    _stop = threading.Event()

    # ---------------------------------------------------------------- retention
    @classmethod
    def prune_old_records(cls, now: Optional[float] = None) -> Dict[str, int]:
        """Delete audit rows and finished jobs older than the configured retention windows."""
        cfg = SettingsService.get_all()["maintenance"]
        now = now or time.time()
        audit_cutoff = now - cfg["log_retention_days"] * DAY
        job_cutoff = now - cfg["job_retention_days"] * DAY

        conn = get_db_connection()
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        with conn:
            audits = conn.execute("DELETE FROM audit_logs WHERE timestamp < ?", (audit_cutoff,)).rowcount
            jobs = conn.execute(
                f"DELETE FROM jobs WHERE status IN ({placeholders}) AND COALESCE(completed_at, added_at, 0) < ?",
                (*TERMINAL_STATUSES, job_cutoff),
            ).rowcount
        if audits or jobs:
            logger.info("Retention: removed %d audit rows and %d finished jobs", audits, jobs)
        return {"audit_logs": audits, "jobs": jobs}

    # ------------------------------------------------------------------ backups
    @classmethod
    def backup_if_due(cls, now: Optional[float] = None) -> Optional[str]:
        """Create an 'auto' backup when the newest backup is older than the configured interval."""
        cfg = SettingsService.get_all()["maintenance"]
        if not cfg["backup_enabled"]:
            return None
        now = now or time.time()
        backups = BackupService.list_backups()
        newest = backups[0]["created"] if backups else 0.0
        if now - newest < cfg["backup_interval_hours"] * 3600:
            return None

        path = BackupService.create_database_backup(reason="auto")
        record_audit("BACKUP", f"Scheduled backup created: {path.name}")

        autos = [b for b in BackupService.list_backups() if "_auto_" in b["filename"]]
        for stale in autos[KEEP_AUTO_BACKUPS:]:
            try:
                from pathlib import Path
                Path(stale["path"]).unlink()
            except OSError as exc:
                logger.warning("Could not remove old backup %s: %s", stale["filename"], exc)
        return path.name

    # ------------------------------------------------------------------ updates
    @classmethod
    def check_updates_if_due(cls, now: Optional[float] = None) -> Optional[dict]:
        """Daily, and only when an update feed URL is configured."""
        from src.update_service import UpdateService
        if not SettingsService.get_all()["maintenance"]["update_feed_url"]:
            return None
        last = (UpdateService.last_result() or {}).get("checked_at", 0)
        if (now or time.time()) - last < DAY:
            return None
        result = UpdateService.check()
        if result.get("available"):
            logger.info("Update available: v%s (installed v%s)", result.get("latest"), result.get("current"))
        return result

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def run_once(cls) -> None:
        for task in (cls.prune_old_records, cls.backup_if_due, cls.check_updates_if_due):
            try:
                task()
            except Exception as exc:
                logger.error("Maintenance task %s failed: %s", task.__name__, exc)

    @classmethod
    def _loop(cls) -> None:
        while not cls._stop.is_set():
            cls.run_once()
            cls._stop.wait(TICK_SECONDS)

    @classmethod
    def start(cls) -> None:
        """Start housekeeping and (if enabled) the release monitor. Idempotent."""
        cfg = SettingsService.get_all()["maintenance"]
        if cfg["monitor_enabled"]:
            SchedulerService.start_scheduler_thread(interval_sec=cfg["monitor_interval_minutes"] * 60)
        if cls._thread and cls._thread.is_alive():
            return
        cls._stop.clear()
        cls._thread = threading.Thread(target=cls._loop, daemon=True, name="MaintenanceLoop")
        cls._thread.start()
        logger.info("Maintenance service started")

    @classmethod
    def stop(cls) -> None:
        cls._stop.set()
        SchedulerService.stop_scheduler_thread()
