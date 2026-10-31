import logging
import re
from typing import Any, Dict, List, Optional
import yt_dlp

from src.config_loader import get_config
from src.providers.base import MusicProvider, NormalizedItem, ProviderName

logger = logging.getLogger("YouTubeMusicProvider")


class YouTubeMusicProvider(MusicProvider):
    """
    YouTube Music provider adapter.
    Resolves YouTube Music tracks, albums, playlists, and artists with provider-specific
    IDs and canonical URLs. Never silently substitutes for Spotify items.
    """

    def __init__(self):
        self._ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "socket_timeout": 10,
        }

    @property
    def provider_name(self) -> str:
        return ProviderName.YTMUSIC.value

    @property
    def display_name(self) -> str:
        return "YouTube Music"

    def is_enabled(self) -> bool:
        cfg = get_config()
        return cfg.get("providers", {}).get("ytmusic_enabled", True)

    def search(
        self,
        query: str,
        entity_types: Optional[List[str]] = None,
        limit: int = 15
    ) -> Dict[str, List[NormalizedItem]]:
        """
        Searches YouTube Music for tracks, albums, artists, and playlists.
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
            logger.info("YouTube Music provider is disabled in settings.")
            return results

        # Clean query
        q = query.strip()
        search_target = f"https://music.youtube.com/search?q={q}"

        try:
            with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                info = ydl.extract_info(search_target, download=False)
                entries = info.get("entries", []) if info else []

            # If music search returned limited entries, supplement with targeted music query
            if len(entries) < 3:
                with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                    fallback_info = ydl.extract_info(f"ytsearch{limit}:{q} audio", download=False)
                    if fallback_info and fallback_info.get("entries"):
                        entries.extend(fallback_info.get("entries", []))

            seen_ids = set()
            for e in entries:
                if not e:
                    continue
                item_id = e.get("id") or ""
                url = e.get("url") or ""
                title = e.get("title") or ""
                if not item_id and url:
                    m_vid = re.search(r"v=([a-zA-Z0-9_-]{11})", url)
                    if m_vid:
                        item_id = m_vid.group(1)

                if not item_id or item_id in seen_ids or not title:
                    continue
                seen_ids.add(item_id)

                uploader = e.get("uploader") or e.get("channel") or e.get("artist") or "YouTube Music Artist"
                dur = e.get("duration")
                dur_ms = int(dur * 1000) if dur else None
                thumb = e.get("thumbnail") or f"https://i.ytimg.com/vi/{item_id}/hqdefault.jpg"

                # Categorize based on URL or browse type
                if "browse/MPRE" in url or "browse/album" in url:
                    results["albums"].append(NormalizedItem(
                        provider=self.provider_name,
                        provider_item_id=item_id,
                        entity_type="album",
                        title=title,
                        primary_creator=uploader,
                        thumbnail_url=thumb,
                        canonical_url=f"https://music.youtube.com/browse/{item_id}",
                        source_metadata=e
                    ))
                elif "browse/UC" in url or "channel/" in url:
                    results["artists"].append(NormalizedItem(
                        provider=self.provider_name,
                        provider_item_id=item_id,
                        entity_type="artist",
                        title=title,
                        primary_creator=title,
                        thumbnail_url=thumb,
                        canonical_url=f"https://music.youtube.com/channel/{item_id}",
                        source_metadata=e
                    ))
                elif "playlist?list=" in url:
                    results["playlists"].append(NormalizedItem(
                        provider=self.provider_name,
                        provider_item_id=item_id,
                        entity_type="playlist",
                        title=title,
                        primary_creator=uploader,
                        thumbnail_url=thumb,
                        canonical_url=f"https://music.youtube.com/playlist?list={item_id}",
                        source_metadata=e
                    ))
                else:
                    norm_track = NormalizedItem(
                        provider=self.provider_name,
                        provider_item_id=item_id,
                        entity_type="track",
                        title=title,
                        primary_creator=uploader,
                        album=e.get("album"),
                        duration_ms=dur_ms,
                        thumbnail_url=thumb,
                        canonical_url=f"https://music.youtube.com/watch?v={item_id}",
                        capabilities={
                            "can_play": True,
                            "can_embed": True,
                            "can_queue": True,
                            "playback_type": "youtube_embed",
                            "embed_url": f"https://www.youtube.com/embed/{item_id}?autoplay=1"
                        },
                        source_metadata=e
                    )
                    results["tracks"].append(norm_track)

            return results
        except Exception as ex:
            logger.warning(f"YouTube Music search error: {ex}")
            return results

    def resolve_url(self, url: str) -> Optional[NormalizedItem]:
        """
        Rule A/B: Resolves exact YouTube Music entity without substitution.
        """
        if "music.youtube.com" not in url:
            return None

        # Extract track/video ID
        m_v = re.search(r"v=([a-zA-Z0-9_-]{11})", url)
        if m_v:
            return self.get_item("track", m_v.group(1))

        # Extract playlist ID
        m_list = re.search(r"list=([a-zA-Z0-9_-]+)", url)
        if m_list:
            return self.get_item("playlist", m_list.group(1))

        # Extract browse ID (album or artist)
        m_browse = re.search(r"browse/([a-zA-Z0-9_-]+)", url)
        if m_browse:
            return self.get_item("album", m_browse.group(1))

        return None

    def get_item(self, entity_type: str, item_id: str) -> Optional[NormalizedItem]:
        """
        Retrieves specific YouTube Music item metadata.
        """
        target_url = f"https://music.youtube.com/watch?v={item_id}" if entity_type == "track" else f"https://music.youtube.com/playlist?list={item_id}"
        try:
            with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    return None

                title = info.get("title", "Unknown Track")
                artist = info.get("artist") or info.get("uploader") or "YouTube Music Artist"
                album = info.get("album")
                dur = info.get("duration")
                dur_ms = int(dur * 1000) if dur else None
                thumb = info.get("thumbnail") or f"https://i.ytimg.com/vi/{item_id}/hqdefault.jpg"

                return NormalizedItem(
                    provider=self.provider_name,
                    provider_item_id=item_id,
                    entity_type=entity_type,
                    title=title,
                    primary_creator=artist,
                    album=album,
                    duration_ms=dur_ms,
                    thumbnail_url=thumb,
                    canonical_url=target_url,
                    capabilities={
                        "can_play": True,
                        "can_embed": True,
                        "can_queue": True,
                        "playback_type": "youtube_embed",
                        "embed_url": f"https://www.youtube.com/embed/{item_id}?autoplay=1"
                    },
                    source_metadata=info
                )
        except Exception as e:
            logger.warning(f"Failed to fetch YouTube Music item {item_id}: {e}")
            return None

    def get_capabilities(self, item: NormalizedItem) -> Dict[str, Any]:
        embed_url = f"https://www.youtube.com/embed/{item.provider_item_id}?autoplay=1"
        return {
            "can_play": True,
            "can_preview": True,
            "can_embed": True,
            "can_queue": True,
            "can_download": False,
            "can_view_meta": True,
            "playback_type": "youtube_embed",
            "embed_url": embed_url,
            "reason_unavailable": "Protected YouTube Music media streams are played via authorized embed."
        }
