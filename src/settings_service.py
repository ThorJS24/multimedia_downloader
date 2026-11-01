"""
Typed settings model.

Every key in SCHEMA has a reader somewhere in the app (or it does not belong here). Updates are
validated and clamped, unknown keys are rejected, and secrets are DPAPI-encrypted at rest and
masked in anything that leaves the process.
"""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config_loader import get_config, save_config

logger = logging.getLogger("SettingsService")

SECRET_PREFIX = "dpapi:"
SECRET_MASK = "********"


@dataclass(frozen=True)
class Field:
    default: Any
    kind: type
    choices: Optional[Tuple[Any, ...]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    secret: bool = False
    check: Optional[Callable[[Any], bool]] = None


def _bitrate_ok(v: str) -> bool:
    return v.endswith("k") and v[:-1].isdigit() and 32 <= int(v[:-1]) <= 512


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")     # datetime.weekday(): Monday == 0


def parse_hhmm(value: str) -> Optional[int]:
    """'07:30' -> minutes since midnight, or None when it is not a valid 24-hour time."""
    try:
        hours, minutes = value.strip().split(":")
        h, m = int(hours), int(minutes)
    except (AttributeError, ValueError):
        return None
    return h * 60 + m if 0 <= h <= 23 and 0 <= m <= 59 and len(minutes) == 2 else None


def days_valid(value: str) -> bool:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return bool(parts) and all(p in DAYS for p in parts)


SCHEMA: Dict[str, Dict[str, Field]] = {
    "general": {
        "first_run_completed": Field(False, bool),
        "restore_queue_on_startup": Field(True, bool),
    },
    "audio": {
        "output_format": Field("mp3", str, choices=("mp3", "ogg", "flac", "opus", "m4a", "aac", "wav")),
        "mp3_bitrate": Field("320k", str, check=_bitrate_ok),
        "audio_quality": Field("VERY_HIGH", str, choices=("VERY_HIGH", "HIGH", "NORMAL")),
        "normalize_loudness": Field(False, bool),
        "save_cover_art": Field(True, bool),
        "embed_lyrics": Field(True, bool),
    },
    "naming": {
        "template": Field("{artist} - {title}.{ext}", str, check=lambda v: "{title}" in v and "{ext}" in v),
        "folder_organization": Field("playlist", str, choices=("playlist", "artist_album", "flat")),
        "collision_strategy": Field("skip", str, choices=("skip", "overwrite", "rename")),
    },
    "performance": {
        "max_workers": Field(1, int, min=1, max=4),
        "rate_limit_min_delay": Field(3.0, float, min=0, max=60),
        "rate_limit_max_delay": Field(7.0, float, min=0, max=120),
        "retry_limit": Field(3, int, min=0, max=10),
    },
    "spotify": {
        "username": Field("", str),
        "password": Field("", str, secret=True),
        "client_id": Field("", str),
        "client_secret": Field("", str, secret=True),
        "credentials_file": Field(".cache/credentials.json", str),
    },
    "watcher": {
        "enabled": Field(False, bool),
        "polling_interval_sec": Field(5.0, float, min=0.5, max=3600),
        "settling_delay_sec": Field(2.0, float, min=0.1, max=600),
        "recursive": Field(True, bool),
        "use_polling_fallback": Field(False, bool),
    },
    "schedule": {
        # Only start new downloads inside this window; running and queued jobs are never lost.
        "enabled": Field(False, bool),
        "start": Field("01:00", str, check=lambda v: parse_hhmm(v) is not None),
        "end": Field("07:00", str, check=lambda v: parse_hhmm(v) is not None),
        "days": Field(",".join(DAYS), str, check=days_valid),
    },
    "notifications": {
        "enabled": Field(True, bool),            # desktop notification when a batch of downloads finishes
        "minimize_to_tray": Field(False, bool),  # minimising hides the window; the tray icon brings it back
    },
    "maintenance": {
        "monitor_enabled": Field(True, bool),
        "monitor_interval_minutes": Field(60, int, min=5, max=1440),
        "backup_enabled": Field(True, bool),
        "backup_interval_hours": Field(24, int, min=1, max=168),
        "log_retention_days": Field(90, int, min=7, max=3650),
        "job_retention_days": Field(90, int, min=7, max=3650),
        # Update checks are opt-in: empty = never contact anything.
        "update_feed_url": Field("", str, check=lambda v: v == "" or (v.startswith("https://") and len(v) <= 500)),
        "update_require_signature": Field(True, bool),
    },
}

# Sections that older versions persisted and that nothing reads any more.
_DROPPED_SECTIONS = ("matching", "fetching")

# Legacy "downloader" keys -> (section, key). Older configs kept a duplicate section; it is folded
# into audio/performance once (legacy values win) and then removed.
_LEGACY_DOWNLOADER_KEYS = {
    "output_format": ("audio", "output_format"),
    "mp3_bitrate": ("audio", "mp3_bitrate"),
    "audio_quality": ("audio", "audio_quality"),
    "save_cover_art": ("audio", "save_cover_art"),
    "embed_lyrics": ("audio", "embed_lyrics"),
    "rate_limit_min_delay": ("performance", "rate_limit_min_delay"),
    "rate_limit_max_delay": ("performance", "rate_limit_max_delay"),
    "retry_count": ("performance", "retry_limit"),
}


def defaults() -> Dict[str, Dict[str, Any]]:
    return {sec: {k: f.default for k, f in fields.items()} for sec, fields in SCHEMA.items()}


def _coerce(name: str, field: Field, value: Any) -> Any:
    """Validate and normalise one value, raising ValueError with a user-facing message."""
    try:
        if field.kind is bool:
            if isinstance(value, str):
                if value.strip().lower() in ("true", "1", "yes", "on"):
                    return True
                if value.strip().lower() in ("false", "0", "no", "off"):
                    return False
                raise ValueError
            return bool(value)
        if field.kind is int:
            if isinstance(value, bool):
                raise ValueError
            value = int(float(value))
        elif field.kind is float:
            if isinstance(value, bool):
                raise ValueError
            value = float(value)
        else:
            if not isinstance(value, str):
                raise ValueError
            value = value.strip()
    except (TypeError, ValueError):
        raise ValueError(f"{name}: expected {field.kind.__name__}") from None

    if field.choices is not None and value not in field.choices:
        raise ValueError(f"{name}: must be one of {', '.join(map(str, field.choices))}")
    if field.min is not None and value < field.min:
        value = field.kind(field.min)
    if field.max is not None and value > field.max:
        value = field.kind(field.max)
    if field.check is not None and not field.check(value):
        raise ValueError(f"{name}: invalid value")
    return value


def _encrypt(value: str) -> str:
    from src.security import SecurityManager

    if not value or value.startswith(SECRET_PREFIX):
        return value
    return SECRET_PREFIX + SecurityManager.encrypt_secret(value)


def _decrypt(value: str) -> str:
    from src.security import SecurityManager

    if not value or not value.startswith(SECRET_PREFIX):
        return value  # legacy plaintext; re-encrypted on the next read-migrate
    return SecurityManager.decrypt_secret(value[len(SECRET_PREFIX):])


def protect(value: str) -> str:
    """Encrypt a secret for storage on this computer (used by plugin packs for webhook addresses and the like)."""
    return _encrypt(value)


def unprotect(value: str) -> str:
    return _decrypt(value)


def _migrate(raw: dict) -> bool:
    """Bring an on-disk config up to the current shape. Returns True if `raw` changed."""
    changed = False

    legacy = raw.pop("downloader", None)
    if legacy is not None:
        changed = True
        for old_key, (section, new_key) in _LEGACY_DOWNLOADER_KEYS.items():
            if old_key in legacy:
                raw.setdefault(section, {})[new_key] = legacy[old_key]

    for section in _DROPPED_SECTIONS:
        if raw.pop(section, None) is not None:
            changed = True

    for section, fields in SCHEMA.items():
        values = raw.get(section)
        if not isinstance(values, dict):
            continue
        for key in list(values):
            if key not in fields:
                del values[key]  # e.g. language/theme/default_engine: no reader, so no setting
                changed = True
        for key, field in fields.items():
            if field.secret and isinstance(values.get(key), str) and values[key] and not values[key].startswith(SECRET_PREFIX):
                try:
                    values[key] = _encrypt(values[key])
                    changed = True
                except Exception:
                    logger.warning("Could not encrypt %s.%s; leaving it unchanged", section, key)
    return changed


class SettingsService:
    _listeners: List[Callable[[], None]] = []

    @classmethod
    def on_change(cls, callback: Callable[[], None]) -> None:
        """Run `callback` after settings are saved (used by services that cache or act on them)."""
        cls._listeners.append(callback)

    @classmethod
    def _changed(cls) -> None:
        for callback in cls._listeners:
            try:
                callback()
            except Exception:
                logger.warning("A settings-change listener failed", exc_info=True)

    @classmethod
    def _load(cls) -> dict:
        raw = get_config()
        if _migrate(raw):
            save_config(raw)
        return raw

    @classmethod
    def get_all(cls, reveal: bool = False) -> dict:
        """Merged settings. Secrets are masked unless `reveal=True` (in-process use only)."""
        raw = cls._load()
        merged: Dict[str, Any] = {}
        for section, fields in SCHEMA.items():
            candidate = raw.get(section)
            stored: Dict[str, Any] = candidate if isinstance(candidate, dict) else {}
            out: Dict[str, Any] = {}
            for key, field in fields.items():
                value = stored.get(key, field.default)
                if field.secret:
                    value = _decrypt(value) if reveal else (SECRET_MASK if value else "")
                else:
                    try:
                        value = _coerce(f"{section}.{key}", field, value)
                    except ValueError:
                        value = field.default
                out[key] = value
            merged[section] = out
        for section, value in raw.items():  # pass-through (paths, providers, ...)
            merged.setdefault(section, value)
        return merged

    @classmethod
    def get_secret(cls, section: str, key: str) -> str:
        return cls.get_all(reveal=True)[section][key]

    @classmethod
    def update(cls, new_settings: dict) -> dict:
        """Validate and persist a partial update. Raises ValueError on bad input."""
        if not isinstance(new_settings, dict):
            raise ValueError("settings payload must be an object")
        raw = cls._load()
        errors = []
        staged: Dict[str, Dict[str, Any]] = {}
        for section, values in new_settings.items():
            if section not in SCHEMA:
                errors.append(f"unknown section '{section}'")
                continue
            if not isinstance(values, dict):
                errors.append(f"{section}: expected an object")
                continue
            for key, value in values.items():
                field = SCHEMA[section].get(key)
                if field is None:
                    errors.append(f"unknown setting '{section}.{key}'")
                    continue
                if field.secret:
                    if value in (None, "", SECRET_MASK):  # blank or echoed mask = keep current
                        continue
                    if not isinstance(value, str):
                        errors.append(f"{section}.{key}: expected str")
                        continue
                    staged.setdefault(section, {})[key] = _encrypt(value.strip())
                    continue
                try:
                    staged.setdefault(section, {})[key] = _coerce(f"{section}.{key}", field, value)
                except ValueError as e:
                    errors.append(str(e))
        if errors:
            raise ValueError("; ".join(errors))

        perf = staged.get("performance", {})
        current_perf = raw.get("performance", {}) if isinstance(raw.get("performance"), dict) else {}
        lo = perf.get("rate_limit_min_delay", current_perf.get("rate_limit_min_delay", 3.0))
        hi = perf.get("rate_limit_max_delay", current_perf.get("rate_limit_max_delay", 7.0))
        if lo > hi:
            raise ValueError("performance: rate_limit_min_delay cannot exceed rate_limit_max_delay")

        for section, values in staged.items():
            if not isinstance(raw.get(section), dict):
                raw[section] = {}
            raw[section].update(values)
        save_config(raw)
        cls._changed()
        return cls.get_all()

    @classmethod
    def reset_defaults(cls) -> dict:
        """Restore every setting except credentials and unrelated sections (paths, providers)."""
        raw = cls._load()
        spotify_section = raw.get("spotify")
        keep_spotify: Dict[str, Any] = spotify_section if isinstance(spotify_section, dict) else {}
        fresh: Dict[str, Any] = {sec: vals for sec, vals in defaults().items() if sec != "spotify"}
        fresh["spotify"] = keep_spotify
        for section, value in raw.items():
            if section not in SCHEMA:
                fresh[section] = value
        save_config(fresh)
        cls._changed()
        return cls.get_all()

    @classmethod
    def export_sanitized(cls) -> dict:
        """Settings safe to share or back up: secrets and the account name removed."""
        data = cls.get_all()
        data["spotify"] = {k: ("" if k in ("username", "client_secret", "password") else v)
                           for k, v in data["spotify"].items()}
        return data
