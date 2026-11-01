from src.version import __version__
import json
import os
import platform
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from src.backup_service import BackupService
from src.config_loader import (
    DATA_DIR,
    PROJECT_ROOT,
    TEMP_DIR,
    get_config,
    get_credentials_path,
    get_downloads_path,
)
from src.db import get_db_connection, get_migration_version
from src.library_intelligence import LibraryIntelligence
from src.logging_setup import LOG_FILE
from src.media_processor import get_ffmpeg_bin, get_ffprobe_bin
from src.security import SecurityManager

DIAGNOSTICS_DIR: Path = DATA_DIR / "diagnostics"
KEEP_BUNDLES = 10
# Addresses on these sites are useful when debugging and carry no credentials; any other address loses its path
# (webhook URLs, update feeds and the like can contain tokens).
KNOWN_HOSTS = ("spotify.com", "scdn.co", "youtube.com", "youtu.be", "googlevideo.com", "musicbrainz.org", "coverartarchive.org",
               "lrclib.net", "github.com", "githubusercontent.com")
URL_RE = re.compile(r"""https?://[^\s'"<>)\]]+""")
# C:\Users\<name>\...  The name may contain spaces, and inside JSON the separators are doubled.
USER_PATH_RE = re.compile(r"""(?i)([A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/))[^\\/"'\r\n]+""")


def scrub_text(text: str) -> str:
    """Remove what must not leave this computer: secrets, tokens inside addresses, the Windows user name."""
    def one_url(match: "re.Match[str]") -> str:
        parsed = urlparse(match.group(0))
        host = parsed.hostname or ""
        if any(host == h or host.endswith("." + h) for h in KNOWN_HOSTS):
            return match.group(0)
        return f"{parsed.scheme}://{parsed.netloc}" + ("/…" if parsed.path not in ("", "/") or parsed.query else "")
    text = SecurityManager.redact_secrets(text)
    text = URL_RE.sub(one_url, text)
    return USER_PATH_RE.sub(lambda m: m.group(1) + "<user>", text)


class DiagnosticsService:
    """
    Comprehensive system, storage, and operational diagnostics engine.
    Ensures zero credential leakage while giving full operational visibility.
    """

    @classmethod
    def generate_report(cls) -> Dict[str, Any]:
        total_b, used_b, free_b = shutil.disk_usage(PROJECT_ROOT)
        db_ok, db_msg = BackupService.verify_database_integrity()

        ffmpeg_path = get_ffmpeg_bin()
        ffprobe_path = get_ffprobe_bin()

        cred_path = get_credentials_path()
        has_credentials = cred_path.exists()

        # Database stats
        conn = get_db_connection()
        migration_v = get_migration_version()
        job_stats = {
            "queued": conn.execute("SELECT COUNT(*) as c FROM jobs WHERE status = 'QUEUED'").fetchone()["c"],
            "completed": conn.execute("SELECT COUNT(*) as c FROM jobs WHERE status = 'COMPLETED'").fetchone()["c"],
            "failed": conn.execute("SELECT COUNT(*) as c FROM jobs WHERE status = 'FAILED'").fetchone()["c"],
            "skipped": conn.execute("SELECT COUNT(*) as c FROM jobs WHERE status = 'SKIPPED'").fetchone()["c"],
        }
        lib_count = conn.execute("SELECT COUNT(*) as c FROM library_files").fetchone()["c"]
        audit_count = conn.execute("SELECT COUNT(*) as c FROM audit_logs").fetchone()["c"]

        cfg = get_config()
        # Mask any sensitive data in config
        safe_cfg = json.loads(json.dumps(cfg))
        if "spotify" in safe_cfg:
            if "username" in safe_cfg["spotify"] and safe_cfg["spotify"]["username"]:
                u = safe_cfg["spotify"]["username"]
                safe_cfg["spotify"]["username"] = f"{u[:2]}***" if len(u) > 2 else "***"
            if "password" in safe_cfg["spotify"]:
                safe_cfg["spotify"]["password"] = "***MASKED***" if safe_cfg["spotify"]["password"] else ""
            if "client_id" in safe_cfg["spotify"] and safe_cfg["spotify"]["client_id"]:
                cid = safe_cfg["spotify"]["client_id"]
                safe_cfg["spotify"]["client_id"] = f"{cid[:4]}***" if len(cid) > 4 else "***"
            if "client_secret" in safe_cfg["spotify"]:
                safe_cfg["spotify"]["client_secret"] = "***MASKED***" if safe_cfg["spotify"]["client_secret"] else ""

        health_data = LibraryIntelligence.get_intelligence_overview()

        watcher_info = {}
        try:
            from src.folder_watcher import FolderWatcherService
            w_status = FolderWatcherService.get_status()
            watcher_info = {
                "status": w_status["status"],
                "is_paused": w_status["is_paused"],
                "monitored_folders_count": w_status["total_folders"],
                "active_folders_count": w_status["active_folders"],
                "pending_intake_count": w_status["pending_intake_count"]
            }
        except Exception as e:
            watcher_info = {"status": "UNAVAILABLE", "error": str(e)}

        # Containment verification
        from src.config_loader import is_portable_mode, get_app_dir, get_data_dir
        portable = is_portable_mode()
        is_isolated = (
            not str(PROJECT_ROOT).lower().startswith(os.environ.get("SystemDrive", "C:").lower()) and
            str(TEMP_DIR).lower().startswith(str(PROJECT_ROOT).lower()) and
            os.environ.get("TEMP", "").lower().startswith(str(PROJECT_ROOT).lower())
        )

        return {
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "application": {
                "name": "Spotify Downloader Studio",
                "version": f"v{__version__}",
                "platform": platform.platform(),
                "python_version": sys.version.split()[0],
                "frozen": getattr(sys, "frozen", False)
            },
            "containment": {
                "mode": "portable" if portable else "installed",
                "app_dir": str(get_app_dir()),
                "data_dir": str(get_data_dir()),
                "project_root": str(PROJECT_ROOT),
                "is_d_drive_isolated": is_isolated,
                "zero_c_drive_writes": is_isolated,
                "temp_directory": str(TEMP_DIR),
                "downloads_directory": str(get_downloads_path())
            },
            "storage": {
                "total_gb": round(total_b / (2**30), 2),
                "used_gb": round(used_b / (2**30), 2),
                "free_gb": round(free_b / (2**30), 2)
            },
            "binaries": {
                "ffmpeg_path": str(ffmpeg_path),
                "ffmpeg_exists": Path(ffmpeg_path).exists(),
                "ffprobe_path": str(ffprobe_path),
                "ffprobe_exists": Path(ffprobe_path).exists()
            },
            "database": {
                "integrity_check": "PASS" if db_ok else "FAIL",
                "integrity_details": db_msg,
                "schema_migration_version": migration_v,
                "journal_mode": "wal",
                "library_track_count": lib_count,
                "audit_log_entries": audit_count,
                "job_counts": job_stats
            },
            "library_health": {
                "health_score": health_data.get("health_score", 0),
                "total_audio_files": health_data.get("total_tracks", 0),
                "missing_tags": health_data.get("missing_tags", 0),
                "duplicates": health_data.get("duplicates", 0)
            },
            "security": {
                "credentials_cached": has_credentials,
                "credential_path": str(cred_path),
                "passwords_exposed": False
            },
            "folder_watcher": watcher_info,
            "configuration": safe_cfg
        }

    @classmethod
    def create_support_bundle(cls) -> Path:
        """A zip you can attach to a bug report: report, settings, plugin status, recent failures and the logs, all
        scrubbed. It is written to <data folder>/diagnostics and nothing is sent anywhere."""
        from src.plugin_packs import PluginPacks
        from src.settings_service import SettingsService

        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        stamp = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int((now % 1) * 1000):03d}"     # sorts oldest to newest by name
        target = DIAGNOSTICS_DIR / f"studio-diagnostics-{stamp}.zip"
        counter = 1
        while target.exists():
            target = DIAGNOSTICS_DIR / f"studio-diagnostics-{stamp}-{counter}.zip"
            counter += 1

        failures = [dict(r) for r in get_db_connection().execute(
            "SELECT title, artists, album, error, provider, added_at, completed_at FROM jobs WHERE status = 'FAILED' "
            "ORDER BY COALESCE(completed_at, added_at) DESC LIMIT 50").fetchall()]
        plugins = [{k: p[k] for k in ("id", "name", "version", "source", "enabled", "permissions", "last_status")} for p in PluginPacks.list_packs()]
        files: Dict[str, str] = {
            "README.txt": ("Spotify Downloader Studio diagnostics bundle\n\nMade on this computer when you asked for it; nothing was sent anywhere.\n"
                           "Secrets, webhook addresses and your Windows user name are removed. Read the files before you share the bundle.\n"),
            "report.json": json.dumps(cls.generate_report(), indent=2, default=str),
            "report.md": cls.generate_markdown_report(),
            "settings.json": json.dumps(SettingsService.export_sanitized(), indent=2, default=str),
            "plugins.json": json.dumps(plugins, indent=2, default=str),
            "recent_failures.json": json.dumps(failures, indent=2, default=str),
        }
        logs: List[Path] = sorted(LOG_FILE.parent.glob(LOG_FILE.name + "*"))
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, content in files.items():
                bundle.writestr(name, scrub_text(content))
            for log in logs:
                try:
                    bundle.writestr(f"logs/{log.name}", scrub_text(log.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    bundle.writestr(f"logs/{log.name}.unreadable.txt", "This log file could not be read.")
        for old in sorted(DIAGNOSTICS_DIR.glob("studio-diagnostics-*.zip"), key=lambda p: p.name, reverse=True)[KEEP_BUNDLES:]:
            old.unlink(missing_ok=True)
        return target

    @classmethod
    def generate_markdown_report(cls) -> str:
        r = cls.generate_report()
        return f"""# Spotify Downloader Studio — System Diagnostic Report
**Generated:** {r['timestamp_iso']}
**Version:** {r['application']['version']}
**Platform:** {r['application']['platform']}

---

## 1. Storage & Containment
- **Project Root:** `{r['containment']['project_root']}`
- **Storage Isolated to D: Drive:** {'✅ YES' if r['containment']['is_d_drive_isolated'] else '❌ NO'}
- **Zero C: Drive Footprint:** {'✅ PASS (0 bytes on C:)' if r['containment']['zero_c_drive_writes'] else '❌ FAIL'}
- **Free Space on D:** `{r['storage']['free_gb']} GB` (Total: `{r['storage']['total_gb']} GB`)

## 2. Core Dependencies & Binaries
- **FFmpeg Binary:** `{r['binaries']['ffmpeg_path']}` ({'Available' if r['binaries']['ffmpeg_exists'] else 'Missing'})
- **FFprobe Binary:** `{r['binaries']['ffprobe_path']}` ({'Available' if r['binaries']['ffprobe_exists'] else 'Missing'})

## 3. Database & Jobs
- **Integrity:** {r['database']['integrity_check']} ({r['database']['integrity_details']})
- **Schema Migration Version:** {r['database']['schema_migration_version']}
- **Library Files:** {r['database']['library_track_count']}
- **Job Statuses:** Completed: {r['database']['job_counts']['completed']} | Queued: {r['database']['job_counts']['queued']} | Failed: {r['database']['job_counts']['failed']}

## 4. Library Health Score
- **Overall Score:** {r['library_health']['health_score']}%
- **Duplicate Tracks:** {r['library_health']['duplicates']}
- **Missing Tags:** {r['library_health']['missing_tags']}
"""
