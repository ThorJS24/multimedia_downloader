import logging
import re
from typing import Dict, List, Optional, Tuple
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from src.settings_service import SettingsService

logger = logging.getLogger("SpotifyApi")


class SpotifyApiHelper:
    """
    Interacts with the official Spotify Web API using spotipy (if client credentials provided)
    or parses Spotify URLs/IDs into clean identifiers.
    """

    @staticmethod
    def parse_spotify_link(url_or_uri: str) -> Tuple[str, str]:
        """
        Parses a Spotify link or URI into (type, id).
        Examples:
          - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=... -> ('playlist', '37i9dQZF1DXcBWIGoYBM5M')
          - spotify:track:4cOdK2wGLETKBW3PvgPWqT -> ('track', '4cOdK2wGLETKBW3PvgPWqT')
        """
        clean_input = url_or_uri.strip()

        # URI format
        uri_match = re.match(r"^spotify:(playlist|track|album):([a-zA-Z0-9]+)", clean_input)
        if uri_match:
            return uri_match.group(1), uri_match.group(2)

        # URL format
        url_match = re.search(r"open\.spotify\.com/(playlist|track|album)/([a-zA-Z0-9]+)", clean_input)
        if url_match:
            return url_match.group(1), url_match.group(2)

        # Raw base62 ID assumption
        if re.match(r"^[a-zA-Z0-9]{22}$", clean_input):
            return "unknown", clean_input

        raise ValueError(f"Unrecognized Spotify URL or URI: {url_or_uri}")

    def __init__(self):
        creds = SettingsService.get_all(reveal=True)["spotify"]
        client_id = creds["client_id"]
        client_secret = creds["client_secret"]

        self.sp: Optional[spotipy.Spotify] = None
        if client_id and client_secret:
            try:
                auth_manager = SpotifyClientCredentials(
                    client_id=client_id,
                    client_secret=client_secret
                )
                # urllib3 retry inside spotipy honours Retry-After on 429 and backs off on 5xx
                self.sp = spotipy.Spotify(auth_manager=auth_manager, retries=3, status_retries=3, backoff_factor=1.0, requests_timeout=10)
            except Exception as e:
                logger.warning("Could not initialize Spotify Web API: %s", e)

    def has_web_api(self) -> bool:
        return self.sp is not None

    def client(self) -> spotipy.Spotify:
        """The Web API client; callers guard with has_web_api() and fall back when it raises."""
        if self.sp is None:
            raise RuntimeError("Spotify Web API is not configured")
        return self.sp

    def get_playlist_metadata(self, playlist_id: str) -> Tuple[str, List[Dict]]:
        """
        Retrieves full playlist metadata and track items via Spotify Web API.
        Handles pagination for playlists with >100 tracks.
        """
        if not self.sp:
            raise RuntimeError("Spotify Web API credentials not configured.")

        playlist = self.sp.playlist(playlist_id)
        playlist_name = playlist.get("name", f"Playlist_{playlist_id}")

        tracks = []
        results = self.sp.playlist_items(playlist_id, additional_types=["track"])

        while results:
            for item in results.get("items", []):
                t = item.get("track")
                if not t or not t.get("id"):
                    continue

                artists = [a["name"] for a in t.get("artists", [])]
                album = t.get("album", {})
                images = album.get("images", [])
                cover_url = images[0]["url"] if images else None

                track_info = {
                    "id": t["id"],
                    "uri": t["uri"],
                    "title": t["name"],
                    "artists": artists,
                    "album": album.get("name", ""),
                    "year": album.get("release_date", "")[:4] if album.get("release_date") else "",
                    "track_number": t.get("track_number", 1),
                    "disc_number": t.get("disc_number", 1),
                    "isrc": t.get("external_ids", {}).get("isrc", ""),
                    "cover_url": cover_url,
                    "duration_ms": t.get("duration_ms", 0)
                }
                tracks.append(track_info)

            if results.get("next"):
                results = self.sp.next(results)
            else:
                break

        return playlist_name, tracks
