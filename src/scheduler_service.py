import logging
import threading
import time
from typing import Any, Dict, List, Optional

from src.db import get_db_connection, record_audit
from src.metadata_service import MetadataService

logger = logging.getLogger("SchedulerService")


class SchedulerService:
    """
    Artist and playlist monitoring engine, release calendar generator,
    and scheduled automated task runner.
    """

    _worker_thread: Optional[threading.Thread] = None
    _stop_event = threading.Event()
    _interval_seconds: int = 3600  # Default 1 hour
    _is_running: bool = False

    @classmethod
    def add_monitored_artist(cls, artist_id: str, artist_name: str, auto_download: bool = False) -> bool:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO monitored_artists (id, name, spotify_id, last_checked, auto_download) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, auto_download=excluded.auto_download",
                (artist_id, artist_name, artist_id, 0, 1 if auto_download else 0)
            )
        record_audit("MONITOR", f"Added artist '{artist_name}' to monitoring list.")
        return True

    @classmethod
    def remove_monitored_artist(cls, artist_id: str) -> bool:
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM monitored_artists WHERE id = ?", (artist_id,))
        return True

    @classmethod
    def get_monitored_artists(cls) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM monitored_artists ORDER BY name ASC").fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def check_artist_releases(cls, artist_id: str) -> List[Dict[str, Any]]:
        """
        Queries Spotify metadata for the artist's releases and adds new items to calendar.
        """
        ms = MetadataService()
        conn = get_db_connection()
        artist_data = ms.resolve_item("artist", artist_id, force_refresh=True)
        artist_name = artist_data.get("name", "Unknown Artist")
        discography = artist_data.get("discography", [])

        new_items = []
        now = time.time()

        for alb in discography:
            alb_id = alb.get("id")
            if not alb_id: continue

            # Check if already recorded
            existing = conn.execute("SELECT id FROM release_calendar WHERE id = ?", (alb_id,)).fetchone()
            if not existing:
                title = alb.get("name", "")
                rel_type = alb.get("type", "album")
                rel_date = alb.get("release_date", "2026")
                cover = alb.get("cover_url", "")
                spot_url = alb.get("spotify_url", f"https://open.spotify.com/album/{alb_id}")

                with conn:
                    conn.execute(
                        "INSERT INTO release_calendar (id, artist_id, artist_name, title, release_type, release_date, cover_url, spotify_url, status, detected_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?)",
                        (alb_id, artist_id, artist_name, title, rel_type, rel_date, cover, spot_url, now)
                    )
                new_items.append({
                    "id": alb_id,
                    "artist_name": artist_name,
                    "title": title,
                    "release_date": rel_date,
                    "type": rel_type
                })

        with conn:
            conn.execute("UPDATE monitored_artists SET last_checked = ? WHERE id = ?", (now, artist_id))

        if new_items:
            record_audit("MONITOR", f"Discovered {len(new_items)} new releases for '{artist_name}'")
        return new_items

    @classmethod
    def get_release_calendar(cls) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM release_calendar ORDER BY release_date DESC").fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def run_all_monitors(cls) -> Dict[str, Any]:
        """
        Runs check for all monitored artists and playlists.
        """
        artists = cls.get_monitored_artists()
        total_discovered = 0
        details = []

        for a in artists:
            try:
                new_rels = cls.check_artist_releases(a["id"])
                total_discovered += len(new_rels)
                details.append({"artist": a["name"], "new_releases": len(new_rels)})
            except Exception as e:
                logger.error(f"Failed to check artist {a['name']}: {e}")

        return {
            "timestamp": time.time(),
            "artists_checked": len(artists),
            "new_releases_found": total_discovered,
            "details": details
        }

    @classmethod
    def start_scheduler_thread(cls, interval_sec: int = 3600):
        if cls._is_running:
            return
        cls._interval_seconds = interval_sec
        cls._stop_event.clear()
        cls._worker_thread = threading.Thread(target=cls._run_loop, daemon=True, name="MonitorScheduler")
        cls._worker_thread.start()
        cls._is_running = True
        logger.info(f"Background monitoring scheduler started (Interval: {interval_sec}s)")

    @classmethod
    def stop_scheduler_thread(cls):
        if cls._is_running and cls._stop_event:
            cls._stop_event.set()
            cls._is_running = False
            logger.info("Background monitoring scheduler stopped.")

    @classmethod
    def _run_loop(cls):
        while not cls._stop_event.is_set():
            try:
                cls.run_all_monitors()
            except Exception as e:
                logger.error(f"Scheduler execution error: {e}")
            cls._stop_event.wait(cls._interval_seconds)
