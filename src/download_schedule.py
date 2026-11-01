"""Download window: only start new downloads at the hours (and on the days) you choose.

The queue asks `allowed_now()` before it starts a job. A job that is already running is never interrupted, and
queued jobs are kept, so nothing is lost: they simply wait for the window. "Download now anyway" lifts the schedule
until the app restarts or you switch it back on.
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from src.settings_service import DAYS, SettingsService, parse_hhmm

logger = logging.getLogger("DownloadSchedule")

DEFAULT_DAYS = ",".join(DAYS)
CONFIG_CACHE_SEC = 5.0


class DownloadSchedule:
    _lock = threading.Lock()
    _cached_cfg: Optional[Dict[str, Any]] = None
    _cached_at = 0.0
    override = False          # "download now anyway"; in memory on purpose, so a restart returns to the schedule

    @classmethod
    def invalidate(cls) -> None:
        with cls._lock:
            cls._cached_cfg = None

    @classmethod
    def config(cls) -> Dict[str, Any]:
        with cls._lock:
            if cls._cached_cfg is None or time.monotonic() - cls._cached_at > CONFIG_CACHE_SEC:
                cls._cached_cfg = dict(SettingsService.get_all()["schedule"])
                cls._cached_at = time.monotonic()
            return dict(cls._cached_cfg)

    @staticmethod
    def _window(cfg: Dict[str, Any]) -> tuple:
        start = parse_hhmm(cfg["start"])
        end = parse_hhmm(cfg["end"])
        return (0 if start is None else start, 0 if end is None else end)

    @staticmethod
    def _day_ok(cfg: Dict[str, Any], weekday: int) -> bool:
        return DAYS[weekday] in {p.strip() for p in cfg["days"].split(",")}

    @classmethod
    def in_window(cls, now: datetime, cfg: Dict[str, Any]) -> bool:
        """Is `now` inside the window? Days belong to the day a window *starts* on, so a night window that crosses
        midnight (22:00-06:00) on Friday runs until Saturday 06:00. Equal start and end mean the whole day."""
        start, end = cls._window(cfg)
        minute = now.hour * 60 + now.minute
        today_ok = cls._day_ok(cfg, now.weekday())
        if start == end:
            return today_ok
        if start < end:
            return today_ok and start <= minute < end
        yesterday_ok = cls._day_ok(cfg, (now.weekday() - 1) % 7)
        return (today_ok and minute >= start) or (yesterday_ok and minute < end)

    @classmethod
    def allowed_now(cls, now: Optional[datetime] = None, cfg: Optional[Dict[str, Any]] = None) -> bool:
        cfg = cfg or cls.config()
        if not cfg["enabled"] or cls.override:
            return True
        return cls.in_window(now or datetime.now(), cfg)

    @classmethod
    def next_start(cls, now: Optional[datetime] = None, cfg: Optional[Dict[str, Any]] = None) -> Optional[datetime]:
        """When the next window opens, or None when the schedule is off or a window is open right now."""
        cfg = cfg or cls.config()
        now = now or datetime.now()
        if not cfg["enabled"] or cls.in_window(now, cfg):
            return None
        start, _ = cls._window(cfg)
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            candidate = day.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
            if candidate > now and cls._day_ok(cfg, candidate.weekday()):
                return candidate
        return None

    @classmethod
    def describe(cls, now: Optional[datetime] = None) -> Dict[str, Any]:
        """What the interface shows: whether downloads may start, and when they will."""
        cfg = cls.config()
        now = now or datetime.now()
        waiting_until = None if (not cfg["enabled"] or cls.override) else cls.next_start(now, cfg)
        return {
            "enabled": bool(cfg["enabled"]),
            "override": cls.override,
            "allowed_now": cls.allowed_now(now, cfg),
            "window": f"{cfg['start']}–{cfg['end']}",
            "next_start": waiting_until.isoformat(timespec="minutes") if waiting_until else None,
            "next_start_text": waiting_until.strftime("%A %H:%M") if waiting_until and waiting_until.date() != now.date() else (waiting_until.strftime("today %H:%M") if waiting_until else None),
        }

    @classmethod
    def set_override(cls, on: bool) -> None:
        cls.override = bool(on)
        logger.info("Download schedule %s", "overridden: downloads may start now" if on else "back in effect")


SettingsService.on_change(DownloadSchedule.invalidate)
