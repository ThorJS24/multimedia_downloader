import logging
from typing import Any, Dict, List, Optional

from src.config_loader import get_config
from src.link_parser import LinkParser
from src.metadata_service import MetadataService
from src.providers.base import MusicProvider, NormalizedItem, ProviderName
from src.spotify_api import SpotifyApiHelper

logger = logging.getLogger("SpotifyProvider")


class SpotifyProvider(MusicProvider):
    """
    Authoritative Spotify music provider.
    Handles Spotify tracks, albums, playlists, and artists with exact ID preservation,
    zero silent substitution, and official capability disclosure.
    """

    def __init__(self):
        self.metadata_service = MetadataService()
        self.web_api = SpotifyApiHelper()

    @property
    def provider_name(self) -> str:
        return ProviderName.SPOTIFY.value

    @property
    def display_name(self) -> str:
        return "Spotify"

    def is_enabled(self) -> bool:
        cfg = get_config()
        return cfg.get("providers", {}).get("spotify_enabled", True)

    def search(
        self,
        query: str,
        entity_types: Optional[List[str]] = None,
        limit: int = 15
    ) -> Dict[str, List[NormalizedItem]]:
        """
        Searches Spotify catalogue. Uses official Web API if configured,
        or indicates authentication limitation.
        """
        results: Dict[str, List[NormalizedItem]] = {
            "tracks": [],
            "albums": [],
            "artists": [],
            "playlists": [],
            "videos": []
        }
        if not query or not query.strip():
            return results

        if not self.is_enabled():
            logger.info("Spotify provider is disabled in settings.")
            return results

        # 1. If Web API is authorized, use official multi-type search
        if self.web_api.has_web_api():
            try:
                types = "track,album,artist,playlist"
                sp_res = self.web_api.sp.search(q=query, type=types, limit=limit)

                # Tracks
                for t in sp_res.get("tracks", {}).get("items", []):
                    results["tracks"].append(self._normalize_track(t))

                # Albums
                for a in sp_res.get("albums", {}).get("items", []):
                    results["albums"].append(self._normalize_album(a))

                # Artists
                for art in sp_res.get("artists", {}).get("items", []):
                    results["artists"].append(self._normalize_artist(art))

                # Playlists
                for pl in sp_res.get("playlists", {}).get("items", []):
                    if pl:
                        results["playlists"].append(self._normalize_playlist(pl))

                return results
            except Exception as e:
                logger.warning(f"Spotify Web API search failed: {e}")

        # 2. Zero-key mode without developer keys: Search is constrained,
        # but exact Spotify URLs remain 100% resolvable.
        logger.info("Spotify zero-key mode: Catalogue search requires Client ID/Secret. Exact URLs resolve zero-key.")
        return results

    def resolve_url(self, url: str) -> Optional[NormalizedItem]:
        """
        Rule A: Resolves the exact Spotify entity without ever substituting YouTube.
        """
        parsed = LinkParser.parse_single(url)
        if not parsed.is_valid or parsed.kind not in ("track", "album", "playlist", "artist"):
            return None

        data = self.metadata_service.resolve_item(parsed.kind, parsed.item_id)
        if not data:
            return None

        if parsed.kind == "track":
            return self._normalize_track_metadata(data)
        elif parsed.kind == "album":
            return self._normalize_album_metadata(data)
        elif parsed.kind == "playlist":
            return self._normalize_playlist_metadata(data)
        elif parsed.kind == "artist":
            return self._normalize_artist_metadata(data)
        return None

    def get_item(self, entity_type: str, item_id: str) -> Optional[NormalizedItem]:
        data = self.metadata_service.resolve_item(entity_type, item_id)
        if not data:
            return None
        if entity_type == "track":
            return self._normalize_track_metadata(data)
        elif entity_type == "album":
            return self._normalize_album_metadata(data)
        elif entity_type == "playlist":
            return self._normalize_playlist_metadata(data)
        elif entity_type == "artist":
            return self._normalize_artist_metadata(data)
        return None

    def get_capabilities(self, item: NormalizedItem) -> Dict[str, Any]:
        has_preview = bool(item.capabilities.get("preview_url"))
        return {
            "can_play": has_preview,
            "can_preview": has_preview,
            "can_embed": False,
            "can_queue": True,
            "can_download": False,
            "can_view_meta": True,
            "playback_type": "spotify_preview" if has_preview else "none",
            "preview_url": item.capabilities.get("preview_url"),
            "embed_url": None,
            "stream_url": None,
            "reason_unavailable": (
                "" if has_preview else "Preview unavailable. Protected Spotify full tracks cannot be downloaded directly. Use 'Find alternatives' to select a YouTube Music or YouTube match."
            )
        }

    # --- Normalization Helpers ---

    def _normalize_track(self, t: dict) -> NormalizedItem:
        artists = [a.get("name") for a in t.get("artists", []) if a.get("name")]
        primary = artists[0] if artists else "Unknown Artist"
        album_obj = t.get("album", {})
        images = album_obj.get("images", [])
        cover_url = images[0]["url"] if images else None
        preview_url = t.get("preview_url")
        track_id = t.get("id", "")

        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=track_id,
            entity_type="track",
            title=t.get("name", "Unknown Track"),
            primary_creator=primary,
            contributors=artists[1:] if len(artists) > 1 else [],
            album=album_obj.get("name"),
            duration_ms=t.get("duration_ms"),
            release_date=album_obj.get("release_date"),
            thumbnail_url=cover_url,
            canonical_url=f"https://open.spotify.com/track/{track_id}",
            isrc=t.get("external_ids", {}).get("isrc"),
            track_number=t.get("track_number"),
            disc_number=t.get("disc_number"),
            availability="available",
            capabilities={
                "preview_url": preview_url,
                "can_preview": bool(preview_url),
                "playback_type": "spotify_preview" if preview_url else "none"
            },
            source_metadata=t
        )

    def _normalize_track_metadata(self, data: dict) -> NormalizedItem:
        artists = data.get("artists", ["Unknown Artist"])
        primary = artists[0] if isinstance(artists, list) and artists else str(artists)
        contributors = artists[1:] if isinstance(artists, list) and len(artists) > 1 else []
        track_id = data.get("id", "")

        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=track_id,
            entity_type="track",
            title=data.get("title") or data.get("name", "Unknown Track"),
            primary_creator=primary,
            contributors=contributors,
            album=data.get("album"),
            duration_ms=data.get("duration_ms"),
            release_date=data.get("year") or data.get("release_date"),
            thumbnail_url=data.get("cover_url"),
            canonical_url=data.get("spotify_url") or f"https://open.spotify.com/track/{track_id}",
            isrc=data.get("isrc"),
            track_number=data.get("track_number"),
            disc_number=data.get("disc_number"),
            availability="available",
            capabilities={
                "preview_url": data.get("preview_url"),
                "can_preview": bool(data.get("preview_url")),
                "playback_type": "spotify_preview" if data.get("preview_url") else "none"
            },
            source_metadata=data
        )

    def _normalize_album(self, a: dict) -> NormalizedItem:
        artists = [art.get("name") for art in a.get("artists", []) if art.get("name")]
        primary = artists[0] if artists else "Unknown Artist"
        images = a.get("images", [])
        album_id = a.get("id", "")

        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=album_id,
            entity_type="album",
            title=a.get("name", "Unknown Album"),
            primary_creator=primary,
            contributors=artists[1:] if len(artists) > 1 else [],
            album=a.get("name"),
            release_date=a.get("release_date"),
            thumbnail_url=images[0]["url"] if images else None,
            canonical_url=f"https://open.spotify.com/album/{album_id}",
            track_number=a.get("total_tracks"),
            source_metadata=a
        )

    def _normalize_album_metadata(self, data: dict) -> NormalizedItem:
        album_id = data.get("id", "")
        primary = data.get("artist") or (data.get("artists", ["Unknown Artist"])[0])
        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=album_id,
            entity_type="album",
            title=data.get("name") or data.get("title", "Unknown Album"),
            primary_creator=primary,
            contributors=data.get("artists", [])[1:] if isinstance(data.get("artists"), list) else [],
            album=data.get("name"),
            release_date=str(data.get("year") or data.get("release_date", "")),
            thumbnail_url=data.get("cover_url"),
            canonical_url=f"https://open.spotify.com/album/{album_id}",
            track_number=data.get("total_tracks") or len(data.get("tracks", [])),
            source_metadata=data
        )

    def _normalize_artist(self, art: dict) -> NormalizedItem:
        artist_id = art.get("id", "")
        images = art.get("images", [])
        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=artist_id,
            entity_type="artist",
            title=art.get("name", "Unknown Artist"),
            primary_creator=art.get("name", "Unknown Artist"),
            thumbnail_url=images[0]["url"] if images else None,
            canonical_url=f"https://open.spotify.com/artist/{artist_id}",
            source_metadata=art
        )

    def _normalize_artist_metadata(self, data: dict) -> NormalizedItem:
        artist_id = data.get("id", "")
        name = data.get("name", "Unknown Artist")
        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=artist_id,
            entity_type="artist",
            title=name,
            primary_creator=name,
            thumbnail_url=data.get("cover_url") or data.get("image_url"),
            canonical_url=f"https://open.spotify.com/artist/{artist_id}",
            source_metadata=data
        )

    def _normalize_playlist(self, pl: dict) -> NormalizedItem:
        pl_id = pl.get("id", "")
        images = pl.get("images", [])
        owner = pl.get("owner", {}).get("display_name", "Spotify User")
        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=pl_id,
            entity_type="playlist",
            title=pl.get("name", "Unknown Playlist"),
            primary_creator=owner,
            thumbnail_url=images[0]["url"] if images else None,
            canonical_url=f"https://open.spotify.com/playlist/{pl_id}",
            source_metadata=pl
        )

    def _normalize_playlist_metadata(self, data: dict) -> NormalizedItem:
        pl_id = data.get("id", "")
        return NormalizedItem(
            provider=self.provider_name,
            provider_item_id=pl_id,
            entity_type="playlist",
            title=data.get("name") or data.get("title", "Unknown Playlist"),
            primary_creator=data.get("owner", "Spotify User"),
            thumbnail_url=data.get("cover_url"),
            canonical_url=f"https://open.spotify.com/playlist/{pl_id}",
            track_number=data.get("total_tracks") or len(data.get("tracks", [])),
            source_metadata=data
        )
