"""Best-effort lyrics lookup via LRCLIB (free, no API key). Never raises; returns '' when not found."""
import logging
from typing import Any, Dict, Optional

from src import http_client
from src.version import APP_NAME, __version__

logger = logging.getLogger("LyricsService")

LRCLIB_URL = "https://lrclib.net/api/get"


class LyricsService:
    @staticmethod
    def fetch(artist: str, title: str, album: str = "", duration_sec: Optional[float] = None) -> str:
        if not artist or not title:
            return ""
        params: Dict[str, Any] = {"artist_name": artist, "track_name": title}
        if album:
            params["album_name"] = album
        if duration_sec:
            params["duration"] = int(round(duration_sec))
        try:
            resp = http_client.get(
                LRCLIB_URL,
                params=params,
                headers={"User-Agent": f"{APP_NAME.replace(' ', '')}/{__version__}"},
                timeout=6,
                retries=1,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            return (data.get("plainLyrics") or "").strip()
        except Exception as exc:  # network/JSON problems must never fail a download
            logger.debug("Lyrics lookup failed for %s - %s: %s", artist, title, exc)
            return ""
