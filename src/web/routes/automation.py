from pathlib import Path
import os
import time

from flask import jsonify, request, send_file, Blueprint

from src.config_loader import get_downloads_path
from src.repositories import AuditRepository, LibraryRepository, StagingRepository
from src.folder_watcher import FolderWatcherService
from src.library_indexer import LibraryIndexer
from src.plugin_packs import MANIFEST_MAX_BYTES, PluginPacks
from src.plugin_engine import DownloadArchive, PluginEngine
from src.rules_engine import RulesEngine
from src.scheduler_service import SchedulerService
from src.security import SecurityManager
from src.staging_service import StagingService
from src.web.state import queue_manager

bp = Blueprint("automation", __name__)


# ================= RULES ENGINE API =================
@bp.route("/api/rules", methods=["GET"])
def get_rules():
    return jsonify({"success": True, "rules": RulesEngine.get_rules()})


HOOK_EVENTS = PluginEngine.EVENTS
CONDITIONS_WITHOUT_VALUE = {"all"}


def _inside_app(path: Path) -> bool:
    return PluginEngine.inside_app(path)


def _template_error(action_type: str, template: str):
    if not template:
        return "An action pattern is required."
    if action_type == "flag_issue":
        return None
    parts = template.replace("\\", "/").split("/")
    if template.startswith(("/", "\\")) or ":" in template or ".." in parts:
        return "The pattern must be relative (no drive letters, leading slashes or '..')."
    if action_type == "rename_template" and len(parts) > 1:
        return "A rename pattern is just a file name; use 'Move to folder pattern' for folders."
    return None


@bp.route("/api/rules/meta", methods=["GET"])
def rules_meta():
    return jsonify({
        "success": True,
        "conditions": sorted(RulesEngine.SUPPORTED_CONDITIONS),
        "actions": sorted(RulesEngine.SUPPORTED_ACTIONS),
        "hook_events": list(HOOK_EVENTS),
        "hook_actions": list(PluginEngine.ALLOWED_ACTIONS),
    })


@bp.route("/api/rules/save", methods=["POST"])
def save_rule():
    data = request.json or {}
    name = str(data.get("name", "")).strip()
    ctype = str(data.get("condition_type", "all")).strip()
    cvalue = str(data.get("condition_value", "")).strip()
    atype = str(data.get("action_type", "move_to_template")).strip()
    avalue = str(data.get("action_value", "")).strip()
    try:
        priority = int(data.get("priority", 10))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Priority must be a whole number."}), 400
    if not name or len(name) > 120:
        return jsonify({"success": False, "error": "Give the rule a name (up to 120 characters)."}), 400
    if ctype not in RulesEngine.SUPPORTED_CONDITIONS:
        return jsonify({"success": False, "error": f"Unsupported condition '{ctype}'."}), 400
    if atype not in RulesEngine.SUPPORTED_ACTIONS:
        return jsonify({"success": False, "error": f"Unsupported action '{atype}'."}), 400
    if ctype not in CONDITIONS_WITHOUT_VALUE and not cvalue:
        return jsonify({"success": False, "error": "That condition needs a value."}), 400
    if not 0 <= priority <= 9999:
        return jsonify({"success": False, "error": "Priority must be between 0 and 9999."}), 400
    err = _template_error(atype, avalue)
    if err:
        return jsonify({"success": False, "error": err}), 400
    rule = RulesEngine.save_rule({**data, "name": name, "priority": priority, "condition_type": ctype,
                                  "condition_value": cvalue, "action_type": atype, "action_value": avalue})
    return jsonify({"success": True, "rule": rule})


@bp.route("/api/rules/batches", methods=["GET"])
def rules_batches():
    """One row per executed batch so the UI can offer a one-click undo."""
    return jsonify({"success": True, "batches": RulesEngine.get_batches()})


@bp.route("/api/rules/delete", methods=["POST"])
def delete_rule():
    data = request.json or {}
    rule_id = data.get("id")
    if not rule_id:
        return jsonify({"error": "Missing rule id", "success": False}), 400
    success = RulesEngine.delete_rule(rule_id)
    return jsonify({"success": success})


@bp.route("/api/rules/preview", methods=["POST"])
def preview_rules():
    data = request.json or {}
    rules = data.get("rules")
    file_paths = data.get("paths")
    result = RulesEngine.preview(rules=rules, file_paths=file_paths)
    return jsonify({"success": True, "preview": result})


@bp.route("/api/rules/execute", methods=["POST"])
def execute_rules():
    data = request.json or {}
    actions = data.get("actions", [])
    policy = data.get("collision_policy", "skip")
    if policy not in ("skip", "rename", "overwrite"):
        return jsonify({"success": False, "error": "Unknown collision policy."}), 400
    if not isinstance(actions, list) or not actions:
        return jsonify({"success": False, "error": "There is nothing to apply."}), 400
    downloads = get_downloads_path().resolve()
    known = {r["path"] for r in LibraryRepository.tracks_by_paths([str(a.get("source_path", "")) for a in actions if isinstance(a, dict)])}
    safe = []
    for item in actions:
        if not isinstance(item, dict) or not item.get("source_path") or not item.get("target_path"):
            return jsonify({"success": False, "error": "Malformed action in the request."}), 400
        src, dst = str(item["source_path"]), Path(str(item["target_path"])).resolve()
        if src not in known or not (dst == downloads or dst.is_relative_to(downloads)):
            return jsonify({"success": False, "error": "Only indexed library files can be moved, and only inside the downloads folder."}), 400
        safe.append(item)
    res = RulesEngine.execute(safe, collision_policy=policy)
    return jsonify(res)


@bp.route("/api/rules/rollback", methods=["POST"])
def rollback_rules():
    data = request.json or {}
    batch_id = data.get("batch_id")
    res = RulesEngine.rollback(batch_id=batch_id)
    return jsonify(res)


@bp.route("/api/rules/history", methods=["GET"])
def get_rules_history():
    limit = int(request.args.get("limit", 50))
    return jsonify({"success": True, "history": RulesEngine.get_history(limit=limit)})


# ================= UNIFIED OPERATIONS CENTER API =================
@bp.route("/api/operations/center", methods=["GET"])
def get_operations_center():
    # 1. Queue Status
    q_snap = queue_manager.get_snapshot()
    q_jobs = q_snap.get("jobs", [])
    active_q_count = q_snap.get("counts", {}).get("active", 0)

    # 2. Watcher Status
    watcher_status = FolderWatcherService.get_status()

    # 3. Indexer Status
    scan_status = LibraryIndexer.get_scan_status()

    # 4. Staging Status
    staged_count = StagingRepository.staged_count()

    # 5. Recent Audit Logs
    recent_logs = AuditRepository.recent(20)

    # 6. Overall System Health
    health = {
        "status": "HEALTHY",
        "queue_active": active_q_count,
        "watcher_running": watcher_status.get("status") == "RUNNING",
        "indexing_running": scan_status.get("status") == "RUNNING",
        "staged_items": staged_count,
        "monitored_folders": watcher_status.get("total_folders", 0),
        "recent_audits": [dict(row) for row in recent_logs]
    }

    return jsonify({
        "success": True,
        "health": health,
        "queue": {
            "total": len(q_jobs),
            "active": active_q_count,
            "is_paused": q_snap.get("is_paused", False)
        },
        "watcher": watcher_status,
        "indexer": scan_status
    })


@bp.route("/api/stream", methods=["GET"])
def stream_audio():
    file_path = request.args.get("path", "")
    if not file_path:
        return jsonify({"error": "Path missing", "success": False}), 400

    p = Path(file_path).resolve()
    down_dir = get_downloads_path().resolve()

    if not SecurityManager.validate_safe_path(p, down_dir):
        return jsonify({"error": "Access denied: Path outside downloads directory", "success": False}), 403

    if not p.exists():
        return jsonify({"error": "File not found"}), 404

    return send_file(str(p))


@bp.route("/api/open-folder", methods=["POST"])
def open_folder():
    down_dir = get_downloads_path()
    try:
        if os.name == "nt":
            os.startfile(str(down_dir))
        return jsonify({"success": True, "path": str(down_dir)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ================= ARTIST & PLAYLIST MONITORING =================
@bp.route("/api/monitors/artists", methods=["GET"])
def list_monitored_artists():
    return jsonify({"artists": SchedulerService.get_monitored_artists()})


@bp.route("/api/monitors/artists/add", methods=["POST"])
def add_artist_to_monitor():
    data = request.json or {}
    artist_id = data.get("id", "").strip()
    name = data.get("name", "").strip()
    auto_dl = data.get("auto_download", False)
    if not artist_id or not name:
        return jsonify({"error": "Artist ID and name required"}), 400
    SchedulerService.add_monitored_artist(artist_id, name, auto_dl)
    return jsonify({"success": True})


@bp.route("/api/monitors/artists/delete", methods=["POST"])
def delete_monitored_artist():
    data = request.json or {}
    artist_id = data.get("id", "")
    SchedulerService.remove_monitored_artist(artist_id)
    return jsonify({"success": True})


@bp.route("/api/monitors/check", methods=["POST"])
def run_monitors_check():
    res = SchedulerService.run_all_monitors()
    return jsonify(res)


@bp.route("/api/monitors/calendar", methods=["GET"])
def get_release_calendar():
    return jsonify({"calendar": SchedulerService.get_release_calendar()})


# ================= PLUGINS & ARCHIVE =================
# ---- plugin packs (declarative add-ons) and the built-in feature switches
@bp.route("/api/plugins/packs", methods=["GET"])
def list_plugin_packs():
    return jsonify({"packs": PluginPacks.list_packs(), "builtin": PluginPacks.builtin_features()})


@bp.route("/api/plugins/packs/import", methods=["POST"])
def import_plugin_pack():
    if request.content_length and request.content_length > MANIFEST_MAX_BYTES * 2:
        return jsonify({"success": False, "error": "That file is too large to be a plugin pack."}), 413
    try:
        pack = PluginPacks.import_pack((request.json or {}).get("manifest"))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "pack": pack})


@bp.route("/api/plugins/packs/configure", methods=["POST"])
def configure_plugin_pack():
    data = request.json or {}
    values = data.get("values") or {}
    if not isinstance(values, dict):
        return jsonify({"success": False, "error": "Settings must be an object."}), 400
    try:
        pack = PluginPacks.configure(str(data.get("id", "")), values, data.get("enabled"))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "pack": pack})


@bp.route("/api/plugins/packs/test", methods=["POST"])
def test_plugin_pack():
    try:
        results = PluginPacks.send_test(str((request.json or {}).get("id", "")))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "results": results})


@bp.route("/api/plugins/packs/remove", methods=["POST"])
def remove_plugin_pack():
    PluginPacks.remove(str((request.json or {}).get("id", "")))
    return jsonify({"success": True})


@bp.route("/api/plugins/hooks", methods=["GET"])
def list_plugin_hooks():
    return jsonify({"hooks": PluginEngine.list_hooks()})


@bp.route("/api/plugins/hooks/register", methods=["POST"])
def register_plugin_hook():
    data = request.json or {}
    hid = data.get("id") or f"hook_{int(time.time())}"
    name = data.get("name", "Custom Hook")
    ev = data.get("event_type", "on_download_complete")
    act = data.get("action_type", "webhook")
    target = str(data.get("target", "")).strip()
    name = str(name).strip()
    if not name or len(name) > 120:
        return jsonify({"success": False, "error": "Give the hook a name (up to 120 characters)."}), 400
    if ev not in HOOK_EVENTS:
        return jsonify({"success": False, "error": f"Unsupported event '{ev}'."}), 400
    if not target:
        return jsonify({"success": False, "error": "A target is required."}), 400
    if act == "copy_to_folder" and not _inside_app(Path(target)):
        return jsonify({"success": False, "error": "Hooks may only copy files into folders inside the application directory."}), 400
    try:
        PluginEngine.register_hook(hid, name, ev, act, target, data.get("config"))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "id": hid})


@bp.route("/api/plugins/hooks/delete", methods=["POST"])
def delete_plugin_hook():
    data = request.json or {}
    PluginEngine.delete_hook(data.get("id", ""))
    return jsonify({"success": True})


@bp.route("/api/archive/list", methods=["GET"])
def get_download_archive():
    limit = int(request.args.get("limit", 100))
    return jsonify({"archive": DownloadArchive.get_archive_list(limit)})


# ================= FOLDER WATCHER & SAFE LIBRARY INTAKE =================
@bp.route("/api/watcher/status", methods=["GET"])
def watcher_get_status():
    from src.folder_watcher import FolderWatcherService
    return jsonify(FolderWatcherService.get_status())


@bp.route("/api/watcher/start", methods=["POST"])
def watcher_start():
    from src.folder_watcher import FolderWatcherService
    ok = FolderWatcherService.start()
    return jsonify({"success": ok, "status": FolderWatcherService.get_status()})


@bp.route("/api/watcher/stop", methods=["POST"])
def watcher_stop():
    from src.folder_watcher import FolderWatcherService
    ok = FolderWatcherService.stop()
    return jsonify({"success": ok, "status": FolderWatcherService.get_status()})


@bp.route("/api/watcher/pause", methods=["POST"])
def watcher_pause():
    from src.folder_watcher import FolderWatcherService
    ok = FolderWatcherService.pause()
    return jsonify({"success": ok, "status": FolderWatcherService.get_status()})


@bp.route("/api/watcher/resume", methods=["POST"])
def watcher_resume():
    from src.folder_watcher import FolderWatcherService
    ok = FolderWatcherService.resume()
    return jsonify({"success": ok, "status": FolderWatcherService.get_status()})


@bp.route("/api/watcher/folders", methods=["GET"])
def watcher_get_folders():
    from src.folder_watcher import FolderWatcherService
    return jsonify({"folders": FolderWatcherService.get_folders()})


@bp.route("/api/watcher/folders/add", methods=["POST"])
def watcher_add_folder():
    from src.folder_watcher import FolderWatcherService
    data = request.json or {}
    path = data.get("path", "").strip()
    label = data.get("label", "").strip()
    recursive = bool(data.get("recursive", True))
    if not path:
        return jsonify({"error": "Path is required"}), 400
    try:
        folder = FolderWatcherService.add_folder(path, label, recursive)
        return jsonify({"success": True, "folder": folder})
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to add watched folder: {e}"}), 500


@bp.route("/api/watcher/folders/remove", methods=["POST"])
def watcher_remove_folder():
    from src.folder_watcher import FolderWatcherService
    data = request.json or {}
    fid = data.get("folder_id", "")
    if not fid:
        return jsonify({"error": "folder_id is required"}), 400
    ok = FolderWatcherService.remove_folder(fid)
    return jsonify({"success": ok})


@bp.route("/api/watcher/folders/toggle", methods=["POST"])
def watcher_toggle_folder():
    from src.folder_watcher import FolderWatcherService
    data = request.json or {}
    fid = data.get("folder_id", "")
    enabled = bool(data.get("enabled", True))
    if not fid:
        return jsonify({"error": "folder_id is required"}), 400
    ok = FolderWatcherService.set_folder_enabled(fid, enabled)
    return jsonify({"success": ok})


@bp.route("/api/watcher/folders/rescan", methods=["POST"])
def watcher_rescan_folder():
    from src.folder_watcher import FolderWatcherService
    data = request.json or {}
    fid = data.get("folder_id")
    if fid:
        res = FolderWatcherService.rescan_folder(fid)
        return jsonify({"success": True, "result": res})
    else:
        results = []
        for f in FolderWatcherService.get_folders():
            if f.get("is_enabled"):
                r = FolderWatcherService.rescan_folder(f["id"])
                results.append(r)
        return jsonify({"success": True, "results": results})


@bp.route("/api/watcher/pending", methods=["GET"])
def watcher_get_pending():
    items = StagingService.get_pending_watcher_items()
    return jsonify({"items": items, "count": len(items)})


@bp.route("/api/watcher/items/dismiss", methods=["POST"])
def watcher_dismiss_item():
    data = request.json or {}
    item_id = data.get("item_id", "")
    if not item_id:
        return jsonify({"error": "item_id is required"}), 400
    ok = StagingService.dismiss_staging_item(item_id)
    return jsonify({"success": ok})


@bp.route("/api/watcher/items/approve", methods=["POST"])
def watcher_approve_items():
    data = request.json or {}
    item_ids = data.get("item_ids")
    if not item_ids and data.get("item_id"):
        item_ids = [data.get("item_id")]
    if not item_ids:
        return jsonify({"error": "No item_ids provided"}), 400

    approved_count = 0
    failed_count = 0
    for iid in item_ids:
        row = StagingRepository.get_item(iid)
        if not row:
            continue
        # Skip if already resolved
        if row["status"] in ("COMMITTED", "DISMISSED"):
            continue

        target_p = Path(row["target_path"])
        reason = row["detection_reason"] if "detection_reason" in row.keys() else ""
        if reason == "REMOVED_LOCAL_FILE":
            StagingRepository.commit_removal(iid, str(target_p))
            approved_count += 1
        else:
            if target_p.exists() and LibraryIndexer.index_single_file(target_p):
                StagingRepository.set_status(iid, "COMMITTED")
                approved_count += 1
            else:
                StagingRepository.set_status(iid, "FAILED")
                failed_count += 1

    return jsonify({"success": True, "approved_count": approved_count, "failed_count": failed_count})
