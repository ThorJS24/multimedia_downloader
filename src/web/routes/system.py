import os
import queue
import shutil
import subprocess
import threading
from typing import Any, Dict
import time
from pathlib import Path

from flask import Response, jsonify, request, Blueprint

from src.backup_service import BACKUPS_DIR, BackupService
from src.config_loader import get_config, save_config, get_credentials_path, get_downloads_path, PROJECT_ROOT
from src.db import record_audit
from src.repositories import JobRepository, LibraryRepository
from src.media_processor import get_ffmpeg_bin
from src.settings_service import SCHEMA, SECRET_MASK, SettingsService
from src.version import __version__, RELEASE_FEED_URL
from src.spotify_direct import SpotifyDirectClient
from src.web.log_stream import format_sse, log_handler
from src.web.state import logger

bp = Blueprint("system", __name__)


# ================= STATUS & AUTH =================
# Opening a Spotify Access Point session is a network round-trip; /api/status is polled by the UI, so
# the result is cached briefly and refreshed on login/logout (or ?refresh=1).
_AUTH_TTL_SEC = 120.0
_auth_lock = threading.Lock()
_auth_state: Dict[str, Any] = {"checked_at": 0.0, "authenticated": False, "account_type": "unknown", "username": ""}


def _probe_auth() -> dict:
    client = SpotifyDirectClient()
    ok = client.is_logged_in() or client.login_with_stored()
    account_type = "unknown"
    if ok and client.session is not None:
        try:
            attrs = getattr(client.session, "user_attributes", None) or {}
            raw = str(attrs.get("type", "")).lower()
            if raw in ("premium", "free", "open"):
                account_type = "free" if raw == "open" else raw
        except Exception:
            logger.debug("Could not read account type", exc_info=True)
    username = get_config().get("spotify", {}).get("username", "") if ok else ""
    return {"checked_at": time.time(), "authenticated": ok, "account_type": account_type, "username": username}


def get_auth_state(refresh: bool = False) -> dict:
    with _auth_lock:
        if refresh or time.time() - _auth_state["checked_at"] > _AUTH_TTL_SEC:
            _auth_state.update(_probe_auth())
        return dict(_auth_state)


def _set_auth_state(**kw) -> None:
    with _auth_lock:
        _auth_state.update(checked_at=time.time(), **kw)


@bp.route("/api/status", methods=["GET"])
def get_system_status():
    cred_file = get_credentials_path()
    total, used, free = shutil.disk_usage(PROJECT_ROOT)
    auth = get_auth_state(refresh=request.args.get("refresh") == "1")

    lib_count = LibraryRepository.count()
    job_count = JobRepository.completed_count()

    return jsonify({
        "project_root": str(PROJECT_ROOT),
        "disk_free_gb": round(free / (2**30), 1),
        "disk_used_gb": round(used / (2**30), 1),
        "disk_total_gb": round(total / (2**30), 1),
        "drive": (PROJECT_ROOT.drive or str(PROJECT_ROOT.anchor)).rstrip("\\/"),
        "authenticated": auth["authenticated"],
        "account_type": auth["account_type"],
        "username": auth["username"],
        "credentials_exist": cred_file.exists(),
        "library_count": lib_count,
        "completed_count": job_count,
        "settings": SettingsService.get_all()
    })


@bp.route("/api/login", methods=["POST"])
def login_spotify():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    client = SpotifyDirectClient()
    success = client.login_with_user_pass(username, password)
    if success:
        cfg = get_config()
        if "spotify" not in cfg: cfg["spotify"] = {}
        cfg["spotify"]["username"] = username
        save_config(cfg)
        record_audit("AUTH", f"User '{username}' authenticated successfully.")
        get_auth_state(refresh=True)
        return jsonify({"success": True, "message": "Authenticated successfully!"})
    else:
        record_audit("AUTH", f"Authentication failed for user '{username}'.", level="WARNING")
        return jsonify({"success": False, "error": "Spotify AP authentication failed"}), 401


@bp.route("/api/logout", methods=["POST"])
def logout_spotify():
    """Forget the cached Spotify session (credentials file + librespot cache). Settings are untouched."""
    removed = []
    cred = get_credentials_path()
    cache_dir = PROJECT_ROOT / ".cache" / "librespot"
    try:
        if cred.exists():
            cred.unlink()
            removed.append(cred.name)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
            removed.append("librespot cache")
    except OSError as e:
        return jsonify({"success": False, "error": f"Could not remove the stored session: {e}"}), 500
    cfg = get_config()
    cfg.setdefault("spotify", {})["username"] = ""
    save_config(cfg)
    _set_auth_state(authenticated=False, account_type="unknown", username="")
    record_audit("AUTH", "Spotify session signed out.")
    return jsonify({"success": True, "removed": removed})


@bp.route("/api/spotify/test-credentials", methods=["POST"])
def test_spotify_credentials():
    data = request.json or {}
    client_id = (data.get("client_id") or "").strip()
    client_secret = (data.get("client_secret") or "").strip()

    if not client_id or not client_secret:
        stored = SettingsService.get_all(reveal=True)["spotify"]
        client_id = client_id or stored["client_id"]
        client_secret = client_secret if client_secret and client_secret != SECRET_MASK else stored["client_secret"]

    if not client_id or not client_secret:
        return jsonify({
            "success": False,
            "connected": False,
            "error": "Client ID and Client Secret cannot be empty."
        }), 400

    try:
        from spotipy.oauth2 import SpotifyClientCredentials
        import spotipy

        auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        res = sp.search(q="track:Bohemian Rhapsody", limit=1, type="track")
        if res and "tracks" in res:
            return jsonify({
                "success": True,
                "connected": True,
                "message": "Spotify Web API connected successfully! Official metadata enabled."
            })
        return jsonify({
            "success": False,
            "connected": False,
            "error": "Spotify API responded with an unexpected payload."
        }), 400
    except Exception as e:
        err_msg = str(e)
        logger.warning(f"Spotify credentials test failed: {err_msg}")
        if "Premium" in err_msg or "403" in err_msg or "blocked" in err_msg.lower():
            display_err = (
                "Spotify Developer Policy: New Web API apps require Spotify Premium. "
                "However, you do NOT need these keys! You can leave Client ID and Secret empty. "
                "The built-in Zero-Key Public Embed Resolver will automatically fetch all songs and playlists for free."
            )
        else:
            display_err = err_msg
        return jsonify({
            "success": False,
            "connected": False,
            "error": display_err
        }), 400


# ================= SETTINGS SERVICE =================
@bp.route("/api/settings", methods=["GET", "POST"])
def handle_settings():
    if request.method == "POST":
        try:
            updated = SettingsService.update(request.json or {})
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        return jsonify({"success": True, "settings": updated})
    return jsonify({"settings": SettingsService.get_all(), "version": __version__, "release_feed_url": RELEASE_FEED_URL})


@bp.route("/api/settings/export", methods=["GET"])
def export_settings():
    """Download settings as JSON, without secrets or the account name."""
    resp = jsonify(SettingsService.export_sanitized())
    resp.headers["Content-Disposition"] = "attachment; filename=studio-settings.json"
    return resp


@bp.route("/api/settings/import", methods=["POST"])
def import_settings():
    """Apply a settings file produced by /api/settings/export (validated; unknown keys rejected)."""
    payload = request.json or {}
    payload.pop("spotify", None)  # credentials are never imported
    for section in [s for s in payload if s not in SCHEMA]:
        payload.pop(section)  # passthrough sections (paths, providers) are not importable
    try:
        updated = SettingsService.update(payload)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "settings": updated})


@bp.route("/api/settings/reset", methods=["POST"])
def reset_settings():
    res = SettingsService.reset_defaults()
    return jsonify({"success": True, "settings": res})


# ================= REAL-TIME SSE LOGS & DIAGNOSTICS =================
@bp.route("/api/logs/stream")
def sse_logs():
    def event_stream():
        q = log_handler.subscribe()
        try:
            # An immediate comment makes the server send the response headers right away (WSGI servers
            # defer them until the first chunk) so the browser's EventSource opens at once.
            yield ": connected\n\n"
            for item in log_handler.get_history():
                yield format_sse(item)
            while True:
                try:
                    record = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"      # also lets the server notice a window that went away
                    continue
                yield format_sse(record)
        except GeneratorExit:
            pass
        finally:
            log_handler.unsubscribe(q)

    resp = Response(event_stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ================= SYSTEM READINESS & BACKUPS =================
@bp.route("/api/system/readiness", methods=["GET"])
def check_readiness():
    total, used, free = shutil.disk_usage(PROJECT_ROOT)
    db_ok, db_status = BackupService.verify_database_integrity()
    ffmpeg_exe = get_ffmpeg_bin()
    ffmpeg_ok = False
    ffmpeg_ver = "unknown"
    try:
        res = subprocess.run(
            [ffmpeg_exe, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        if res.returncode == 0:
            ffmpeg_ok = True
            lines = res.stdout.decode("utf-8", errors="ignore").splitlines()
            ffmpeg_ver = lines[0] if lines else "Installed"
    except Exception as e:
        ffmpeg_ver = str(e)

    # Check write permission strictly in local project temp
    writable = False
    test_file = PROJECT_ROOT / "temp" / f"write_test_{int(time.time())}.tmp"
    try:
        test_file.write_text("ok")
        test_file.unlink()
        writable = True
    except Exception:
        logger.warning("Write test in the temp folder failed (%s)", test_file, exc_info=True)

    cfg = SettingsService.get_all()
    first_run_completed = cfg.get("general", {}).get("first_run_completed", False)

    return jsonify({
        "ready": db_ok and ffmpeg_ok and writable,
        "first_run_needed": not first_run_completed,
        "disk_free_gb": round(free / (2**30), 1),
        "target_drive_writable": writable,
        "ffmpeg_installed": ffmpeg_ok,
        "ffmpeg_version": ffmpeg_ver,
        "ffmpeg_path": ffmpeg_exe,
        "database_integrity": db_ok,
        "database_status": db_status,
        "project_root": str(PROJECT_ROOT),
        "downloads_dir": str(get_downloads_path()),
        "portable_mode": True
    })


def _downloads_dir_error(raw: str):
    """Downloads must live inside the application's own folders (never the user profile or a network share)."""
    from src.config_loader import DATA_DIR
    requested = Path(raw)
    if not requested.is_absolute():
        resolved = (DATA_DIR / requested).resolve()
    else:
        resolved = requested.resolve()
    roots = [PROJECT_ROOT.resolve(), Path(DATA_DIR).resolve()]
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        return ("That folder is outside the application directory. Downloads must stay inside the project folder "
                f"({PROJECT_ROOT}).")
    return None


@bp.route("/api/system/complete-setup", methods=["POST"])
def complete_first_run():
    data = request.json or {}
    downloads_path = (data.get("downloads_path") or "downloads").strip()
    err = _downloads_dir_error(downloads_path)
    if err:
        return jsonify({"success": False, "error": err}), 400

    cfg = get_config()
    cfg.setdefault("paths", {})["download_dir"] = downloads_path
    save_config(cfg)
    SettingsService.update({"general": {"first_run_completed": True}})
    record_audit("SYSTEM", f"First-run setup completed (downloads: {downloads_path})")
    return jsonify({"success": True})


# ================= UPDATES (opt-in) =================
@bp.route("/api/update/check", methods=["GET"])
def update_check():
    from src.update_service import UpdateService
    return jsonify(UpdateService.check())


@bp.route("/api/update/download", methods=["POST"])
def update_download():
    from src.update_service import UpdateService
    try:
        path = UpdateService.download()
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error("Update download failed: %s", e)
        return jsonify({"success": False, "error": f"Download failed: {e}"}), 502
    record_audit("UPDATE", f"Downloaded and verified {path.name}")
    return jsonify({"success": True, "file": path.name})


@bp.route("/api/update/launch", methods=["POST"])
def update_launch():
    from src.update_service import UPDATES_DIR, UpdateService
    name = Path((request.json or {}).get("file", "")).name
    if not name:
        return jsonify({"success": False, "error": "file is required"}), 400
    try:
        UpdateService.launch_installer(UPDATES_DIR / name)
    except PermissionError as e:
        return jsonify({"success": False, "error": str(e)}), 403
    except (ValueError, OSError) as e:
        return jsonify({"success": False, "error": str(e)}), 400
    record_audit("UPDATE", f"Launched installer {name}")
    return jsonify({"success": True})


@bp.route("/api/backups/create", methods=["POST"])
def create_backup():
    try:
        path = BackupService.create_database_backup(reason="manual")
        return jsonify({"success": True, "path": str(path)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/backups/list", methods=["GET"])
def list_backups():
    return jsonify({
        "backups": BackupService.list_backups(),
        "pending": BackupService.pending_restore(),
        "last_restore": BackupService.last_restore(),
        "folder": str(BACKUPS_DIR),
    })


@bp.route("/api/backups/restore", methods=["POST"])
def restore_backup():
    """Stage a restore; it is applied (and the current database saved first) the next time the app starts."""
    filename = ((request.json or {}).get("filename") or "").strip()
    if not filename:
        return jsonify({"success": False, "error": "Choose a backup to restore"}), 400
    try:
        info = BackupService.stage_restore(filename)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error("Staging a restore failed", exc_info=True)
        return jsonify({"success": False, "error": f"Could not stage the restore: {e}"}), 500
    record_audit("BACKUP", f"Restore of {info['source']} staged for the next start")
    return jsonify({"success": True, "pending": info})


@bp.route("/api/backups/restore/cancel", methods=["POST"])
def cancel_restore():
    return jsonify({"success": True, "cancelled": BackupService.cancel_restore()})


@bp.route("/api/backups/restore/dismiss", methods=["POST"])
def dismiss_restore_result():
    BackupService.dismiss_last_restore()
    return jsonify({"success": True})


@bp.route("/api/db/integrity", methods=["GET"])
def check_db_integrity():
    ok, msg = BackupService.verify_database_integrity()
    return jsonify({"ok": ok, "status": msg})


# ================= DESKTOP BRIDGE (folder picker / reveal) =================
@bp.route("/api/system/pick-folder", methods=["POST"])
def pick_folder():
    """Native folder dialog when running inside the desktop window; 501 in a plain browser."""
    from src.desktop_shell import shell_bridge
    if not shell_bridge.available():
        return jsonify({"success": False, "unsupported": True,
                        "error": "The folder picker is only available in the desktop window. Type the path instead."}), 409
    chosen = shell_bridge.pick_folder((request.json or {}).get("start", ""))
    return jsonify({"success": True, "path": chosen or "", "cancelled": not chosen})


@bp.route("/api/system/reveal", methods=["POST"])
def reveal_in_explorer():
    """Show a file (or open a folder) inside the project/downloads tree in Windows Explorer."""
    raw = ((request.json or {}).get("path") or "").strip()
    if not raw:
        return jsonify({"success": False, "error": "Path required"}), 400
    target = Path(raw).resolve()
    allowed_roots = [PROJECT_ROOT.resolve(), get_downloads_path().resolve()]
    if not any(target == r or target.is_relative_to(r) for r in allowed_roots):
        return jsonify({"success": False, "error": "Path is outside the application folders"}), 403
    if not target.exists():
        return jsonify({"success": False, "error": "Path does not exist"}), 404
    try:
        if os.name == "nt":
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)], creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.startfile(str(target))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/settings/downloads-dir", methods=["POST"])
def set_downloads_dir():
    """Change the downloads folder; it must resolve inside the application data directory."""
    raw = ((request.json or {}).get("path") or "").strip()
    if not raw:
        return jsonify({"success": False, "error": "A folder path is required"}), 400
    err = _downloads_dir_error(raw)
    if err:
        return jsonify({"success": False, "error": err}), 400
    cfg = get_config()
    cfg.setdefault("paths", {})["download_dir"] = raw
    save_config(cfg)
    resolved = get_downloads_path()
    record_audit("SYSTEM", f"Downloads folder changed to {resolved}")
    return jsonify({"success": True, "path": str(resolved)})
