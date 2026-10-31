"""
Test isolation. Import this FIRST in every test module (before any `src.*` import).

Points the app at a throwaway data directory inside the project's own `temp/` folder
(so nothing touches the real database, downloads, or config, and nothing lands on C:),
seeds a completed-first-run config, and removes the directory on exit.
"""
import atexit
import json
import os
import shutil
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMP_ROOT = REPO_ROOT / "temp"
_PREFIX = "test_data_"
_STALE_AFTER_SEC = 24 * 3600


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _reap_stale_dirs() -> None:
    """Delete scratch dirs left by earlier runs: owner process gone (a live run keeps its own), or older than a day."""
    if not _TEMP_ROOT.exists():
        return
    now = time.time()
    for d in _TEMP_ROOT.glob("test_data_*"):
        try:
            pid = int(d.name.rsplit("_", 1)[1])
            if (not _pid_alive(pid)) or now - d.stat().st_mtime > _STALE_AFTER_SEC:
                shutil.rmtree(d, ignore_errors=True)
        except (OSError, ValueError, IndexError):
            pass


def _seed_config(data_dir: Path) -> None:
    """Copy the project config (minus credentials) so UI state matches a finished first run."""
    cfg = {}
    src_cfg = REPO_ROOT / "config" / "config.json"
    if src_cfg.exists():
        try:
            cfg = json.loads(src_cfg.read_text(encoding="utf-8"))
        except ValueError:
            cfg = {}
    cfg.setdefault("general", {})["first_run_completed"] = True
    for key in ("username", "password", "client_id", "client_secret"):
        cfg.setdefault("spotify", {})[key] = ""
    target = data_dir / "config" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _cleanup(data_dir: Path) -> None:
    """Close this thread's SQLite handle first; Windows refuses to delete an open WAL database."""
    import sys
    db_mod = sys.modules.get("src.db")
    if db_mod is not None:
        try:
            db_mod.close_db_connection()
        except Exception:
            pass
    try:
        from src.logging_setup import shutdown_logging
        shutdown_logging()
    except Exception:
        pass
    shutil.rmtree(data_dir, ignore_errors=True)


def _activate() -> Path:
    existing = os.environ.get("SPOTIFY_STUDIO_TEST_DATA_DIR")
    if existing:  # already activated in this process (or inherited from a parent test run)
        os.environ["SPOTIFY_STUDIO_DATA_DIR"] = existing
        return Path(existing)

    _reap_stale_dirs()
    data_dir = _TEMP_ROOT / f"{_PREFIX}{os.getpid()}"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    _seed_config(data_dir)
    os.environ["SPOTIFY_STUDIO_DATA_DIR"] = str(data_dir)
    os.environ["SPOTIFY_STUDIO_TEST_DATA_DIR"] = str(data_dir)
    atexit.register(_cleanup, data_dir)
    return data_dir


# Playwright's browsers live inside the project (never %LOCALAPPDATA% on C:).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(REPO_ROOT / ".cache" / "ms-playwright"))

TEST_DATA_DIR = _activate()
