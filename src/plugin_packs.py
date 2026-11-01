"""Plugin packs: add-ons you can switch on, configure and remove. No code from a pack ever runs.

A pack is a small JSON document that *declares* what it wants: which events to react to, what to do (send a web
request or copy a file) and which settings to ask you for (a URL, a folder). Everything it can do is therefore visible
before you turn it on, and the app performs the action itself with its normal safeguards. Settings marked `secret`
(webhook addresses, for example) are encrypted on this computer and never sent back to the page in clear.

Bundled packs ship with the app; you can also import your own `.json` pack (see docs/plugins.md).
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.db import get_db_connection, record_audit
from src.plugin_engine import PluginEngine
from src.settings_service import SECRET_MASK, SettingsService, protect, unprotect

logger = logging.getLogger("PluginPacks")

SCHEMA_VERSION = 1
MANIFEST_MAX_BYTES = 32 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,30}$")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
SETTING_TYPES = ("text", "url", "folder")

EVENT_LABELS = {
    "on_download_complete": "a download finishes",
    "on_download_failed": "a download fails",
    "on_queue_finished": "the queue finishes a batch",
}
TRACK_FIELDS = ("track_id", "title", "artists", "album", "provider", "output_path", "error")
BATCH_FIELDS = ("completed", "failed", "skipped", "cancelled", "total")
PAYLOAD_FIELDS = {"on_download_complete": TRACK_FIELDS, "on_download_failed": TRACK_FIELDS, "on_queue_finished": BATCH_FIELDS}
SAMPLE_PAYLOADS: Dict[str, Dict[str, Any]] = {
    "on_download_complete": {"track_id": "test", "title": "Test track", "artists": ["Test artist"], "album": "Test album",
                             "provider": "spotify", "output_path": "", "error": ""},
    "on_download_failed": {"track_id": "test", "title": "Test track", "artists": ["Test artist"], "album": "Test album",
                           "provider": "spotify", "output_path": "", "error": "This is a test message"},
    "on_queue_finished": {"completed": 3, "failed": 1, "skipped": 0, "cancelled": 0, "total": 4},
}

BUNDLED: List[Dict[str, Any]] = [
    {
        "schema": 1, "id": "discord-notify", "name": "Discord notifications", "version": "1.0.0", "author": "Spotify Downloader Studio",
        "description": "Posts a short message to a Discord channel when a batch of downloads finishes.",
        "settings": [{"key": "webhook_url", "label": "Discord webhook URL", "type": "url", "secret": True, "required": True,
                      "help": "In Discord: channel settings, Integrations, Webhooks, New Webhook, Copy Webhook URL."}],
        "hooks": [{"event": "on_queue_finished", "action": "webhook", "target": "{webhook_url}",
                   "body": {"content": "Spotify Downloader Studio: {completed} downloaded, {failed} failed, {skipped} skipped."}}],
    },
    {
        "schema": 1, "id": "slack-notify", "name": "Slack notifications", "version": "1.0.0", "author": "Spotify Downloader Studio",
        "description": "Posts a short message to a Slack channel when a batch of downloads finishes.",
        "settings": [{"key": "webhook_url", "label": "Slack incoming-webhook URL", "type": "url", "secret": True, "required": True,
                      "help": "Create an Incoming Webhook for your workspace in the Slack app directory and paste its URL."}],
        "hooks": [{"event": "on_queue_finished", "action": "webhook", "target": "{webhook_url}",
                   "body": {"text": "Spotify Downloader Studio: {completed} downloaded, {failed} failed, {skipped} skipped."}}],
    },
    {
        "schema": 1, "id": "ntfy-push", "name": "Phone notifications (ntfy)", "version": "1.0.0", "author": "Spotify Downloader Studio",
        "description": "Sends a push notification to your phone through ntfy when a batch of downloads finishes. Install the free ntfy app and subscribe to your topic.",
        "settings": [
            {"key": "server", "label": "ntfy server", "type": "url", "default": "https://ntfy.sh", "required": True},
            {"key": "topic", "label": "Topic (keep it hard to guess)", "type": "text", "secret": True, "required": True,
             "help": "Anyone who knows the topic name can read the messages, so pick something random."},
        ],
        "hooks": [{"event": "on_queue_finished", "action": "webhook", "target": "{server}/{topic}", "format": "text",
                   "body": "{completed} downloaded, {failed} failed, {skipped} skipped.", "headers": {"Title": "Downloads finished"}}],
    },
    {
        "schema": 1, "id": "home-assistant", "name": "Home Assistant", "version": "1.0.0", "author": "Spotify Downloader Studio",
        "description": "Sends an event to a Home Assistant webhook automation when a batch finishes or a track fails, so you can flash a light or speak a message.",
        "settings": [{"key": "webhook_url", "label": "Home Assistant webhook URL", "type": "url", "secret": True, "required": True,
                      "help": "Looks like http://homeassistant.local:8123/api/webhook/your-id. Create it in an automation with a Webhook trigger."}],
        "hooks": [{"event": "on_queue_finished", "action": "webhook", "target": "{webhook_url}"},
                  {"event": "on_download_failed", "action": "webhook", "target": "{webhook_url}"}],
    },
    {
        "schema": 1, "id": "generic-webhook", "name": "Send events to any web address", "version": "1.0.0", "author": "Spotify Downloader Studio",
        "description": "Sends every download event as JSON to an address you choose, for services such as n8n, Zapier or IFTTT.",
        "settings": [{"key": "url", "label": "Web address", "type": "url", "secret": True, "required": True}],
        "hooks": [{"event": "on_download_complete", "action": "webhook", "target": "{url}"},
                  {"event": "on_download_failed", "action": "webhook", "target": "{url}"},
                  {"event": "on_queue_finished", "action": "webhook", "target": "{url}"}],
    },
    {
        "schema": 1, "id": "copy-to-folder", "name": "Copy finished tracks to a folder", "version": "1.0.0", "author": "Spotify Downloader Studio",
        "description": "Puts a copy of every finished download into one folder, for example a folder your phone or music server syncs from.",
        "settings": [{"key": "folder", "label": "Folder (inside the app folder)", "type": "folder", "required": True,
                      "help": "For safety the folder must be inside the application's own folder."}],
        "hooks": [{"event": "on_download_complete", "action": "copy_to_folder", "target": "{folder}"}],
    },
]

# Features that are built into the app and already have a switch in Preferences; the manager lists them too.
BUILTIN_FEATURES = [
    {"id": "notifications", "name": "Download notifications", "setting": "notifications.enabled", "anchor": "set-schedule",
     "description": "A Windows notification when a batch of downloads finishes."},
    {"id": "tray", "name": "Minimise to tray", "setting": "notifications.minimize_to_tray", "anchor": "set-schedule",
     "description": "Minimising keeps the app running next to the clock."},
    {"id": "schedule", "name": "Download schedule", "setting": "schedule.enabled", "anchor": "set-schedule",
     "description": "Only start new downloads during the hours you choose."},
    {"id": "lyrics", "name": "Embed lyrics", "setting": "audio.embed_lyrics", "anchor": "set-audio",
     "description": "Look up lyrics (LRCLIB) and store them in the file."},
    {"id": "cover", "name": "Save cover art", "setting": "audio.save_cover_art", "anchor": "set-audio",
     "description": "Embed artwork and keep a cover.jpg next to the tracks."},
    {"id": "loudness", "name": "Normalise loudness", "setting": "audio.normalize_loudness", "anchor": "set-audio",
     "description": "Even out volume between tracks while converting."},
    {"id": "watcher", "name": "Folder watcher", "setting": "watcher.enabled", "anchor": "set-automation",
     "description": "Notice new files dropped into watched folders."},
    {"id": "monitor", "name": "Artist monitor", "setting": "maintenance.monitor_enabled", "anchor": "set-automation",
     "description": "Check followed artists for new releases."},
    {"id": "backups", "name": "Automatic backups", "setting": "maintenance.backup_enabled", "anchor": "set-maintenance",
     "description": "Back up the database on a schedule."},
]


# ---------------------------------------------------------------------------------------------- manifests
def _render(template: str, values: Dict[str, Any]) -> str:
    """Replace {name} with a value. Only plain names are looked up, so a template can never reach into objects."""
    def one(match: "re.Match[str]") -> str:
        value = values.get(match.group(1), "")
        return ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
    return PLACEHOLDER_RE.sub(one, template)


def _placeholders(value: Any) -> set:
    if isinstance(value, str):
        return set(PLACEHOLDER_RE.findall(value))
    if isinstance(value, dict):
        return set().union(*(_placeholders(v) for v in value.values())) if value else set()
    return set()


def _text(obj: Dict[str, Any], key: str, limit: int, required: bool = True) -> str:
    value = obj.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValueError(f"'{key}' must be text of at most {limit} characters" + (" and cannot be empty" if required else ""))
    return value.strip()


def validate_manifest(raw: Any) -> Dict[str, Any]:
    """Check a pack document and return a normalised copy. Raises ValueError with a message meant for the person importing it."""
    if not isinstance(raw, dict):
        raise ValueError("A plugin pack must be a JSON object")
    if len(json.dumps(raw)) > MANIFEST_MAX_BYTES:
        raise ValueError("The plugin pack is too large")
    if raw.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported pack format (expected \"schema\": {SCHEMA_VERSION})")
    pack_id = _text(raw, "id", 40)
    if not ID_RE.match(pack_id):
        raise ValueError("'id' must be 3-40 characters: lowercase letters, digits and hyphens")
    manifest: Dict[str, Any] = {
        "schema": SCHEMA_VERSION, "id": pack_id, "name": _text(raw, "name", 80), "version": _text(raw, "version", 20),
        "author": _text(raw, "author", 80, required=False), "description": _text(raw, "description", 400),
    }
    homepage = raw.get("homepage")
    if homepage:
        if not isinstance(homepage, str) or urlparse(homepage).scheme not in ("http", "https") or len(homepage) > 300:
            raise ValueError("'homepage' must be an http(s) address")
        manifest["homepage"] = homepage

    settings, keys = [], set()
    raw_settings = raw.get("settings", [])
    if not isinstance(raw_settings, list) or len(raw_settings) > 10:
        raise ValueError("'settings' must be a list of at most 10 items")
    all_payload = {f for fields in PAYLOAD_FIELDS.values() for f in fields}
    for item in raw_settings:
        if not isinstance(item, dict):
            raise ValueError("Each setting must be an object")
        key = item.get("key", "")
        if not isinstance(key, str) or not KEY_RE.match(key) or key in keys or key in all_payload:
            raise ValueError(f"Setting key '{key}' is invalid, repeated, or clashes with an event field")
        if item.get("type", "text") not in SETTING_TYPES:
            raise ValueError(f"Setting '{key}': type must be one of {', '.join(SETTING_TYPES)}")
        keys.add(key)
        settings.append({"key": key, "label": _text(item, "label", 80), "type": item.get("type", "text"),
                         "secret": bool(item.get("secret", False)), "required": bool(item.get("required", False)),
                         "default": _text(item, "default", 300, required=False), "help": _text(item, "help", 300, required=False)})
    manifest["settings"] = settings

    hooks = []
    raw_hooks = raw.get("hooks")
    if not isinstance(raw_hooks, list) or not 1 <= len(raw_hooks) <= 6:
        raise ValueError("A pack needs between 1 and 6 hooks")
    for hook in raw_hooks:
        if not isinstance(hook, dict):
            raise ValueError("Each hook must be an object")
        event, action = hook.get("event"), hook.get("action")
        if event not in PluginEngine.EVENTS:
            raise ValueError(f"Unknown event '{event}'. Choose from: {', '.join(PluginEngine.EVENTS)}")
        if action not in PluginEngine.ALLOWED_ACTIONS:
            raise ValueError(f"Unknown action '{action}'. Choose from: {', '.join(PluginEngine.ALLOWED_ACTIONS)}")
        if action == "copy_to_folder" and event != "on_download_complete":
            raise ValueError("Copying files only makes sense when a download finishes")
        target = _text(hook, "target", 300)
        clean: Dict[str, Any] = {"event": event, "action": action, "target": target}
        allowed_in_body = keys | set(PAYLOAD_FIELDS[event])
        unknown = _placeholders(target) - keys
        if action == "webhook":
            body, headers = hook.get("body"), hook.get("headers", {})
            if body is not None:
                ok = isinstance(body, str) and len(body) <= 1000
                ok = ok or (isinstance(body, dict) and 0 < len(body) <= 10 and all(isinstance(k, str) and isinstance(v, str) and len(v) <= 500 for k, v in body.items()))
                if not ok:
                    raise ValueError("A hook's 'body' must be text (up to 1000 characters) or an object of text values")
                clean["body"] = body
                unknown |= _placeholders(body) - allowed_in_body
            clean["format"] = "text" if (hook.get("format") == "text" or isinstance(body, str)) else "json"
            if not isinstance(headers, dict) or len(headers) > 5 or not all(
                    isinstance(k, str) and HEADER_RE.match(k) and isinstance(v, str) and len(v) <= 200 and v.isprintable() for k, v in headers.items()):
                raise ValueError("Hook 'headers' must be at most 5 simple name/value pairs")
            clean["headers"] = headers
            unknown |= _placeholders(headers) - allowed_in_body
        if unknown:
            raise ValueError(f"Unknown placeholder(s) {', '.join('{' + u + '}' for u in sorted(unknown))}: use a setting key or an event field")
        hooks.append(clean)
    manifest["hooks"] = hooks
    return manifest


def permissions(manifest: Dict[str, Any], values: Optional[Dict[str, Any]] = None) -> List[str]:
    """Plain-language list of everything the pack will do, so the person can decide before turning it on."""
    shown = {s["key"]: s for s in manifest["settings"]}
    lines = []
    for hook in manifest["hooks"]:
        when = EVENT_LABELS[hook["event"]]
        if hook["action"] == "copy_to_folder":
            lines.append(f"When {when}: copies the file into {_where(hook['target'], shown, values) or 'the folder you choose'}.")
            continue
        fields = sorted(_placeholders(hook.get("body", "")) & set(PAYLOAD_FIELDS[hook["event"]]))
        sends = ("a message containing " + ", ".join(fields)) if fields else ("a fixed message" if "body" in hook else "all details of the event (" + ", ".join(PAYLOAD_FIELDS[hook["event"]]) + ")")
        lines.append(f"When {when}: sends {sends} to {_where(hook['target'], shown, values) or 'the address you enter'}.")
    return lines


def _where(target: str, settings: Dict[str, Dict[str, Any]], values: Optional[Dict[str, Any]]) -> str:
    """The place a hook points at, without revealing secret parts (a webhook URL is a credential)."""
    names = _placeholders(target)
    if names and any(settings[n]["secret"] for n in names if n in settings):
        host = urlparse(_render(target, values or {})).netloc if values and all(values.get(n) for n in names) else ""
        return host or ""
    resolved = _render(target, values or {}) if values else target
    if "{" in resolved:
        return ""
    return urlparse(resolved).netloc or resolved


# ---------------------------------------------------------------------------------------------- state and delivery
# Validated once at import, so a mistake in a bundled pack fails loudly in the tests rather than at runtime.
_BUNDLED_VALID: Dict[str, Dict[str, Any]] = {m["id"]: validate_manifest(m) for m in BUNDLED}


class PluginPacks:
    @classmethod
    def bundled(cls) -> Dict[str, Dict[str, Any]]:
        return _BUNDLED_VALID

    @classmethod
    def _rows(cls) -> Dict[str, Any]:
        return {r["id"]: r for r in get_db_connection().execute("SELECT * FROM plugin_packs").fetchall()}

    @classmethod
    def _manifest(cls, row_or_id: Any, rows: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        pack_id = row_or_id if isinstance(row_or_id, str) else row_or_id["id"]
        if pack_id in cls.bundled():
            return cls.bundled()[pack_id]
        row = (rows or cls._rows()).get(pack_id)
        if row is None or not row["manifest_json"]:
            return None
        try:
            return validate_manifest(json.loads(row["manifest_json"]))
        except ValueError:
            logger.warning("Stored plugin pack %s no longer validates; ignoring it", pack_id)
            return None

    @classmethod
    def _values(cls, manifest: Dict[str, Any], row: Optional[Any], reveal: bool) -> Dict[str, Any]:
        stored = json.loads(row["config_json"]) if row and row["config_json"] else {}
        out: Dict[str, Any] = {}
        for s in manifest["settings"]:
            value = stored.get(s["key"], s["default"])
            if s["secret"] and value:
                value = unprotect(value) if reveal else SECRET_MASK
            out[s["key"]] = value
        return out

    @classmethod
    def list_packs(cls) -> List[Dict[str, Any]]:
        rows = cls._rows()
        packs = []
        for pack_id in [*cls.bundled(), *[i for i in rows if i not in cls.bundled()]]:
            manifest = cls._manifest(pack_id, rows)
            if manifest is None:
                continue
            row = rows.get(pack_id)
            enabled = bool(row and row["enabled"])
            last = None
            if row and row["last_status_json"]:
                try:
                    last = json.loads(row["last_status_json"])
                except ValueError:
                    last = None
            packs.append({**manifest, "source": "bundled" if pack_id in cls.bundled() else "imported", "enabled": enabled,
                          "values": cls._values(manifest, row, reveal=False),
                          "permissions": permissions(manifest, cls._values(manifest, row, reveal=True) if enabled else None),
                          "last_status": last})
        return packs

    @classmethod
    def builtin_features(cls) -> List[Dict[str, Any]]:
        settings = SettingsService.get_all()
        out = []
        for f in BUILTIN_FEATURES:
            section, key = f["setting"].split(".")
            out.append({**f, "enabled": bool(settings[section][key])})
        return out

    @classmethod
    def _clean_values(cls, manifest: Dict[str, Any], given: Dict[str, Any], row: Optional[Any]) -> Dict[str, str]:
        previous = json.loads(row["config_json"]) if row and row["config_json"] else {}
        stored: Dict[str, str] = {}
        for s in manifest["settings"]:
            key, label = s["key"], s["label"]
            value = given.get(key)
            if value is None or (s["secret"] and value == SECRET_MASK):
                stored_value = previous.get(key)
                value = (unprotect(stored_value) if s["secret"] and stored_value else stored_value) if stored_value is not None else s["default"]
            value = str(value or "").strip()
            if not value:
                if s["required"]:
                    raise ValueError(f"Fill in “{label}” first.")
                stored[key] = ""
                continue
            if len(value) > 500:
                raise ValueError(f"“{label}” is too long.")
            if s["type"] == "url":
                parsed = urlparse(value)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    raise ValueError(f"“{label}” must be a web address starting with http:// or https://.")
            elif s["type"] == "folder":
                folder = Path(value)
                if not PluginEngine.inside_app(folder):
                    raise ValueError(f"“{label}” must be a folder inside the application folder.")
                folder.mkdir(parents=True, exist_ok=True)
            stored[key] = protect(value) if s["secret"] else value
        return stored

    @classmethod
    def configure(cls, pack_id: str, values: Dict[str, Any], enabled: Optional[bool] = None) -> Dict[str, Any]:
        """Save a pack's settings; `enabled` turns it on or off at the same time (None leaves it as it is)."""
        rows = cls._rows()
        manifest = cls._manifest(pack_id, rows)
        if manifest is None:
            raise ValueError("That plugin is not installed")
        row = rows.get(pack_id)
        now_enabled = bool(row and row["enabled"]) if enabled is None else bool(enabled)
        stored = cls._clean_values(manifest, values, row) if (now_enabled or values) else json.loads((row["config_json"] if row else None) or "{}")
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO plugin_packs (id, source, enabled, config_json, installed_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled, config_json = excluded.config_json",
                (pack_id, "bundled" if pack_id in cls.bundled() else "imported", int(now_enabled), json.dumps(stored), time.time()))
        record_audit("PLUGIN", f"Plugin '{manifest['name']}' {'turned on' if now_enabled else 'saved (off)'}")
        return next(p for p in cls.list_packs() if p["id"] == pack_id)

    @classmethod
    def import_pack(cls, raw: Any) -> Dict[str, Any]:
        manifest = validate_manifest(raw)
        if manifest["id"] in cls.bundled():
            raise ValueError("A bundled plugin already uses that id; choose a different one")
        conn = get_db_connection()
        existing = cls._rows().get(manifest["id"])
        with conn:
            conn.execute(
                "INSERT INTO plugin_packs (id, source, enabled, config_json, manifest_json, installed_at) VALUES (?, 'imported', 0, '{}', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET manifest_json = excluded.manifest_json",
                (manifest["id"], json.dumps(manifest), time.time()))
        if existing and existing["enabled"]:       # a replaced pack must be reviewed again before it runs
            conn.execute("UPDATE plugin_packs SET enabled = 0 WHERE id = ?", (manifest["id"],))
            conn.commit()
        record_audit("PLUGIN", f"Imported plugin '{manifest['name']}' ({manifest['id']}); it is off until you turn it on")
        return next(p for p in cls.list_packs() if p["id"] == manifest["id"])

    @classmethod
    def remove(cls, pack_id: str) -> None:
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM plugin_packs WHERE id = ?", (pack_id,))
        record_audit("PLUGIN", f"Removed plugin '{pack_id}'" if pack_id not in cls.bundled() else f"Reset plugin '{pack_id}'")

    # ---- running
    @classmethod
    def _deliver(cls, manifest: Dict[str, Any], hook: Dict[str, Any], values: Dict[str, Any], payload: Dict[str, Any]) -> str:
        target = _render(hook["target"], values)
        context = {**values, **payload}
        body = hook.get("body")
        if isinstance(body, str):
            body = _render(body, context)
        elif isinstance(body, dict):
            body = {k: _render(v, context) for k, v in body.items()}
        headers = {k: _render(v, context) for k, v in hook.get("headers", {}).items()}
        return PluginEngine.deliver(hook["action"], target, payload, body=body, as_text=hook.get("format") == "text", headers=headers)

    @classmethod
    def _record(cls, pack_id: str, ok: bool, detail: str) -> None:
        try:
            conn = get_db_connection()
            with conn:
                conn.execute("UPDATE plugin_packs SET last_status_json = ? WHERE id = ?", (json.dumps({"ok": ok, "detail": detail[:300], "at": time.time()}), pack_id))
        except Exception:
            logger.warning("Could not record the delivery status of %s", pack_id, exc_info=True)

    @classmethod
    def fire(cls, event: str, payload: Dict[str, Any]) -> None:
        """Run every enabled pack's hooks for this event. A failing pack never affects another one or the download."""
        rows = cls._rows()
        for pack_id, row in rows.items():
            if not row["enabled"]:
                continue
            manifest = cls._manifest(pack_id, rows)
            if manifest is None:
                continue
            values = cls._values(manifest, row, reveal=True)
            for hook in (h for h in manifest["hooks"] if h["event"] == event):
                try:
                    detail = cls._deliver(manifest, hook, values, payload)
                    cls._record(pack_id, True, detail)
                except Exception as e:
                    logger.warning("Plugin '%s' failed on %s: %s", pack_id, event, e)
                    cls._record(pack_id, False, str(e))

    @classmethod
    def send_test(cls, pack_id: str) -> List[Dict[str, Any]]:
        """Send each hook a sample event now and report what happened (the pack must be switched on and filled in)."""
        rows = cls._rows()
        manifest = cls._manifest(pack_id, rows)
        row = rows.get(pack_id)
        if manifest is None or row is None or not row["enabled"]:
            raise ValueError("Turn the plugin on first, then send a test")
        values = cls._values(manifest, row, reveal=True)
        results = []
        for hook in manifest["hooks"]:
            sample = dict(SAMPLE_PAYLOADS[hook["event"]])
            try:
                if hook["action"] == "copy_to_folder":
                    probe = Path(_render(hook["target"], values)) / ".studio-write-test"
                    probe.write_text("ok", encoding="utf-8")
                    probe.unlink()
                    detail = "The folder can be written to"
                else:
                    detail = cls._deliver(manifest, hook, values, sample)
                results.append({"event": hook["event"], "label": EVENT_LABELS[hook["event"]], "ok": True, "detail": detail})
            except Exception as e:
                results.append({"event": hook["event"], "label": EVENT_LABELS[hook["event"]], "ok": False, "detail": str(e)})
        cls._record(pack_id, all(r["ok"] for r in results), "; ".join(r["detail"] for r in results))
        return results
