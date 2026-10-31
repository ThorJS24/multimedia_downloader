import copy
import json
import os
import sys
import tempfile
import threading
import time
from typing import Any, Dict
from pathlib import Path

if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

BUNDLE_ROOT = Path(getattr(sys, '_MEIPASS', APP_DIR))


def is_portable_mode() -> bool:
    """
    Determines if Spotify Downloader Studio is running in Portable Mode.
    In Portable Mode, all configuration, databases, cache, logs, and temporary
    buffers are stored strictly inside the application directory (APP_DIR).
    """
    if "--portable" in sys.argv:
        return True

    env_p = os.environ.get("SPOTIFY_STUDIO_PORTABLE", "").strip().lower()
    if env_p in ("1", "true", "yes"):
        return True
    if env_p in ("0", "false", "no"):
        return False

    mode = os.environ.get("SPOTIFY_STUDIO_MODE", "").strip().lower()
    if mode == "portable":
        return True
    if mode == "installed":
        return False

    # When packaged/frozen: check for portable marker file in APP_DIR
    if getattr(sys, 'frozen', False):
        return (APP_DIR / "portable.dat").exists() or (APP_DIR / "portable").exists()

    # Development workspace default: portable mode on current workspace
    return True


def get_app_dir() -> Path:
    """Returns the application installation / execution directory."""
    return APP_DIR


def get_data_dir() -> Path:
    """
    Returns the user data directory containing databases, config, logs, cache,
    and temporary files.
    """
    if "--data-dir" in sys.argv:
        try:
            idx = sys.argv.index("--data-dir")
            if idx + 1 < len(sys.argv):
                return Path(sys.argv[idx + 1]).resolve()
        except Exception:
            pass

    custom = os.environ.get("SPOTIFY_STUDIO_DATA_DIR")
    if custom:
        return Path(custom).resolve()

    if is_portable_mode():
        return APP_DIR

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "SpotifyDownloaderStudio"

    return Path.home() / ".spotify_downloader_studio"


DATA_DIR = get_data_dir()
PROJECT_ROOT = DATA_DIR  # Retained for backward-compatibility with modules expecting PROJECT_ROOT

# Prepend local bin directory to PATH so FFmpeg / FFprobe are discovered immediately
LOCAL_BIN = APP_DIR / "bin"
if not LOCAL_BIN.exists() and (BUNDLE_ROOT / "bin").exists():
    LOCAL_BIN = BUNDLE_ROOT / "bin"
if LOCAL_BIN.exists():
    os.environ["PATH"] = str(LOCAL_BIN) + os.pathsep + os.environ.get("PATH", "")

# Enforce local temp directory strictly within DATA_DIR
TEMP_DIR = DATA_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEMP_DIR)
os.environ["TMP"] = str(TEMP_DIR)
tempfile.tempdir = str(TEMP_DIR)

# Enforce local cache directory within DATA_DIR
CACHE_DIR = DATA_DIR / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Enforce local downloads directory within DATA_DIR
DOWNLOADS_DIR = DATA_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

def get_config_path() -> Path:
    return get_data_dir() / "config" / "config.json"


CONFIG_FILE = get_config_path()


_config_cache: Dict[str, Any] = {"key": None, "data": None}
# Serialises reading and replacing config.json inside this process (Windows refuses to rename over a file
# another thread has open).
_config_lock = threading.RLock()


def _file_key(path: Path):
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def get_config() -> dict:
    """Returns a private copy of config.json (re-read only when the file changes on disk)."""
    cfg_file = get_config_path()
    if not cfg_file.exists():
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        default_conf = {
            "spotify": {
                "username": "",
                "password": "",
                "credentials_file": ".cache/credentials.json",
                "client_id": "",
                "client_secret": ""
            },
            "audio": {
                "output_format": "mp3",
                "audio_quality": "VERY_HIGH",
                "mp3_bitrate": "320k",
                "save_cover_art": True,
                "embed_lyrics": True
            },
            "performance": {
                "rate_limit_min_delay": 3.0,
                "rate_limit_max_delay": 7.0,
                "retry_limit": 3
            },
            "paths": {
                "download_dir": "downloads",
                "temp_dir": "temp",
                "cache_dir": ".cache"
            }
        }
        save_config(default_conf)
        return copy.deepcopy(default_conf)

    key = _file_key(cfg_file)
    if key is not None and _config_cache["key"] == key:
        return copy.deepcopy(_config_cache["data"])

    try:
        with _config_lock:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root must be an object")
    except (ValueError, OSError):
        # Never silently discard a user's config: keep the unreadable file for recovery.
        try:
            cfg_file.replace(cfg_file.with_name("config.json.corrupt"))
        except OSError:
            pass
        return {}
    _config_cache["key"], _config_cache["data"] = key, copy.deepcopy(data)
    return data


def save_config(config_data: dict):
    """Atomically replaces config.json (temp file + rename) so a crash can't leave it truncated."""
    cfg_file = get_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_file.with_name(cfg_file.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)
    # On Windows the rename fails with "Access denied" if another thread has config.json open for
    # reading at this exact moment, so retry briefly instead of failing the save.
    for attempt in range(20):
        try:
            with _config_lock:
                os.replace(tmp, cfg_file)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)
    _config_cache["key"] = None


def get_credentials_path() -> Path:
    cfg = get_config()
    cred_rel = cfg.get("spotify", {}).get("credentials_file", ".cache/credentials.json")
    if Path(cred_rel).is_absolute():
        p = Path(cred_rel).resolve()
    else:
        p = (DATA_DIR / cred_rel).resolve()
        if not p.is_relative_to(DATA_DIR.resolve()):
            p = (DATA_DIR / ".cache" / "credentials.json").resolve()
    return p


def get_downloads_path() -> Path:
    cfg = get_config()
    down_rel = cfg.get("paths", {}).get("download_dir", "downloads")
    if Path(down_rel).is_absolute():
        p = Path(down_rel).resolve()
    else:
        p = (DATA_DIR / down_rel).resolve()
        if not p.is_relative_to(DATA_DIR.resolve()):
            p = (DATA_DIR / "downloads").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_temp_path() -> Path:
    return TEMP_DIR
