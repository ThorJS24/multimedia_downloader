import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from src import http_client
from src.config_loader import DATA_DIR, PROJECT_ROOT
from src.db import get_db_connection, record_audit

logger = logging.getLogger("PluginEngine")


class PluginEngine:
    """
    Extensible hook system and download archive manager.
    Supports user-defined post-processing hooks (webhooks, file copy, custom scripts).
    """

    ALLOWED_ACTIONS = ("webhook", "copy_to_folder")
    EVENTS = ("on_download_complete", "on_download_failed", "on_queue_finished")

    @classmethod
    def register_hook(cls, hook_id: str, name: str, event_type: str, action_type: str, target: str, config: Optional[dict] = None) -> bool:
        if action_type not in cls.ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported hook action '{action_type}'. Allowed: {', '.join(cls.ALLOWED_ACTIONS)}")
        if action_type == "webhook" and urlparse(target).scheme not in ("http", "https"):
            raise ValueError("Webhook target must be an http(s) URL")
        conn = get_db_connection()
        cfg_str = json.dumps(config or {})
        with conn:
            conn.execute(
                "INSERT INTO plugin_hooks (id, name, event_type, action_type, target, is_enabled, config_json) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, event_type=excluded.event_type, action_type=excluded.action_type, target=excluded.target, config_json=excluded.config_json",
                (hook_id, name, event_type, action_type, target, cfg_str)
            )
        record_audit("PLUGIN", f"Registered hook '{name}' for event '{event_type}'")
        return True

    @classmethod
    def list_hooks(cls) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM plugin_hooks ORDER BY name ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["config"] = json.loads(d["config_json"]) if d.get("config_json") else {}
            result.append(d)
        return result

    @classmethod
    def delete_hook(cls, hook_id: str) -> bool:
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM plugin_hooks WHERE id = ?", (hook_id,))
        return True

    @classmethod
    def dispatch(cls, event_type: str, payload: Dict[str, Any]) -> None:
        """Fire matching hooks on a background thread so slow webhooks never block a download worker."""
        threading.Thread(
            target=cls.trigger_event, args=(event_type, dict(payload)), daemon=True, name=f"Hook-{event_type}"
        ).start()

    @staticmethod
    def inside_app(path: Path) -> bool:
        """Hooks and plugins may only write inside the application's own folders."""
        resolved = path.resolve()
        return any(resolved == r or resolved.is_relative_to(r) for r in (PROJECT_ROOT.resolve(), Path(DATA_DIR).resolve()))

    @classmethod
    def deliver(cls, action_type: str, target: str, payload: Dict[str, Any], *, body: Any = None,
                as_text: bool = False, headers: Optional[Dict[str, str]] = None) -> str:
        """Perform one hook action and say what happened. Raises with a message that never contains the web address
        (webhook addresses are credentials), only its host."""
        if action_type == "webhook":
            parsed = urlparse(target)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("The web address is not valid")
            send_headers = dict(headers or {})
            kwargs: Dict[str, Any] = {"timeout": 5.0, "retries": 2}
            if body is None:
                kwargs["json"] = payload
            elif as_text:
                kwargs["data"] = str(body).encode("utf-8")
                send_headers.setdefault("Content-Type", "text/plain; charset=utf-8")
            else:
                kwargs["json"] = body
            try:
                resp = http_client.post(target, headers=send_headers or None, **kwargs)
            except requests.Timeout:
                raise RuntimeError(f"{parsed.netloc} did not answer in time") from None
            except requests.RequestException as e:
                raise RuntimeError(f"Could not reach {parsed.netloc} ({type(e).__name__})") from None
            if resp.status_code >= 400:
                raise RuntimeError(f"{parsed.netloc} answered HTTP {resp.status_code}")
            return f"Delivered (HTTP {resp.status_code})"
        if action_type == "copy_to_folder":
            dest_dir = Path(target)
            src_file = Path(payload.get("output_path", ""))
            if not src_file.is_file():
                raise FileNotFoundError("The finished file was not found")
            if not dest_dir.is_dir():
                raise FileNotFoundError("The destination folder does not exist")
            shutil.copy2(src_file, dest_dir / src_file.name)
            return f"Copied {src_file.name}"
        raise ValueError(f"Unsupported hook action '{action_type}'")

    @classmethod
    def trigger_event(cls, event_type: str, payload: Dict[str, Any]):
        """
        Executes every active hook listening for event_type, then the switched-on plugin packs.
        """
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM plugin_hooks WHERE event_type = ? AND is_enabled = 1", (event_type,)).fetchall()

        for r in rows:
            try:
                detail = cls.deliver(r["action_type"], r["target"], payload)
                logger.info("Hook '%s' ran: %s", r["name"], detail)
            except Exception as e:
                logger.warning("Hook '%s' failed: %s", r["name"], e)

        from src.plugin_packs import PluginPacks     # imported here: plugin_packs builds on this module
        try:
            PluginPacks.fire(event_type, payload)
        except Exception:
            logger.error("Running plugin packs failed", exc_info=True)


class DownloadArchive:
    """
    Persistent archive of processed tracks to enable idempotent operations.
    """

    @classmethod
    def is_archived(cls, resource_id: str) -> bool:
        conn = get_db_connection()
        row = conn.execute("SELECT resource_id FROM download_archive WHERE resource_id = ?", (resource_id,)).fetchone()
        return row is not None

    @classmethod
    def record_completed(cls, resource_id: str, resource_type: str, title: str, artist: str, file_path: str):
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO download_archive (resource_id, resource_type, title, artist, file_path, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(resource_id) DO UPDATE SET file_path=excluded.file_path, downloaded_at=excluded.downloaded_at",
                (resource_id, resource_type, title, artist, file_path, time.time())
            )

    @classmethod
    def get_archive_list(cls, limit: int = 100) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM download_archive ORDER BY downloaded_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
