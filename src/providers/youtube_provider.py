import logging
import re
from typing import Any, Dict, List, Optional
import yt_dlp

from src.config_loader import get_config
from src import http_client
from src.providers.base import MusicProvider, NormalizedItem, ProviderName

logger = logging.getLogger("YouTubeProvider")


class YouTubeProvider(MusicProvider):
    """
    YouTube provider adapter.
    Discovers music videos, live performances, acoustic versions, and independent releases.
    Preserves exact YouTube video IDs, channel attributions, canonical URLs, and official embeds.
    Distinguishes videos from audio recordings and strictly adheres to playback capability separation.
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
        return ProviderName.YOUTUBE.value

    @property
    def display_name(self) -> str:
        return "YouTube"

    def is_enabled(self) -> bool:
        cfg = get_config()
        return cfg.get("providers", {}).get("youtube_enabled", True)

    def search(
        self,
        query: str,
        entity_types: Optional[List[str]] = None,
        limit: int = 15
    ) -> Dict[str, List[NormalizedItem]]:
        """
        Searches YouTube for videos and playlists.
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
            logger.info("YouTube provider is disabled in settings.")
            return results

        q = query.strip()
        search_target = f"ytsearch{limit}:{q}"

        try:
            with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                info = ydl.extract_info(search_target, download=False)
                entries = info.get("entries", []) if info else []

            seen_ids = set()
            for e in entries:
                if not e:
                    continue
                item_id = e.get("id") or ""
                title = e.get("title") or ""
                if not item_id or item_id in seen_ids or not title:
                    continue
                seen_ids.add(item_id)

                channel = e.get("uploader") or e.get("channel") or "YouTube Creator"
                dur = e.get("duration")
                dur_ms = int(dur * 1000) if dur else None
                thumb = e.get("thumbnail") or f"https://i.ytimg.com/vi/{item_id}/hqdefault.jpg"
                canonical = f"https://www.youtube.com/watch?v={item_id}"

                item = NormalizedItem(
                    provider=self.provider_name,
                    provider_item_id=item_id,
                    entity_type="video",
                    title=title,
                    primary_creator=channel,
                    duration_ms=dur_ms,
                    thumbnail_url=thumb,
                    canonical_url=canonical,
                    capabilities={
                        "can_play": True,
                        "can_embed": True,
                        "can_queue": True,
                        "playback_type": "youtube_embed",
                        "embed_url": f"https://www.youtube.com/embed/{item_id}?autoplay=1"
                    },
                    source_metadata=e
                )

                # Store under both "videos" and "tracks" category so users searching songs can discover them
                results["videos"].append(item)
                results["tracks"].append(item)

            return results
        except Exception as ex:
            logger.warning(f"YouTube search error: {ex}")
            return results

    def resolve_url(self, url: str) -> Optional[NormalizedItem]:
        """
        Resolves exact YouTube URL preserving video or playlist identity.
        """
        # 1. Match standard watch URL or youtu.be shortlink
        m_v = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        if m_v:
            return self.get_item("video", m_v.group(1))

        # 2. Match playlist URL
        m_list = re.search(r"list=([a-zA-Z0-9_-]+)", url)
        if m_list:
            return self.get_item("playlist", m_list.group(1))

        return None

    def get_item(self, entity_type: str, item_id: str) -> Optional[NormalizedItem]:
        """
        Resolves item metadata using official oEmbed API with in-process yt_dlp fallback.
        """
        canonical_url = f"https://www.youtube.com/watch?v={item_id}"

        # 1. Fast official oEmbed lookup
        if entity_type == "video":
            try:
                oembed_url = f"https://www.youtube.com/oembed?url={canonical_url}&format=json"
                resp = http_client.get(oembed_url, timeout=5)
                resp.raise_for_status()
                data = resp.json()

                return NormalizedItem(
                    provider=self.provider_name,
                    provider_item_id=item_id,
                    entity_type="video",
                    title=data.get("title", "YouTube Video"),
                    primary_creator=data.get("author_name", "YouTube Creator"),
                    thumbnail_url=data.get("thumbnail_url") or f"https://i.ytimg.com/vi/{item_id}/hqdefault.jpg",
                    canonical_url=canonical_url,
                    capabilities={
                        "can_play": True,
                        "can_embed": True,
                        "can_queue": True,
                        "playback_type": "youtube_embed",
                        "embed_url": f"https://www.youtube.com/embed/{item_id}?autoplay=1"
                    },
                    source_metadata=data
                )
            except Exception as e:
                logger.debug(f"oEmbed lookup failed for {item_id}: {e}")

        # 2. Detailed fallback with yt_dlp
        try:
            target = canonical_url if entity_type == "video" else f"https://www.youtube.com/playlist?list={item_id}"
            with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if not info:
                    return None

                title = info.get("title", "YouTube Item")
                channel = info.get("uploader") or info.get("channel") or "YouTube Creator"
                dur = info.get("duration")
                dur_ms = int(dur * 1000) if dur else None
                thumb = info.get("thumbnail") or f"https://i.ytimg.com/vi/{item_id}/hqdefault.jpg"

                return NormalizedItem(
                    provider=self.provider_name,
                    provider_item_id=item_id,
                    entity_type=entity_type,
                    title=title,
                    primary_creator=channel,
                    duration_ms=dur_ms,
                    thumbnail_url=thumb,
                    canonical_url=target,
                    capabilities={
                        "can_play": True,
                        "can_embed": True,
                        "can_queue": True,
                        "playback_type": "youtube_embed",
                        "embed_url": f"https://www.youtube.com/embed/{item_id}?autoplay=1"
                    },
                    source_metadata=info
                )
        except Exception as ex:
            logger.warning(f"Failed to resolve YouTube item {item_id}: {ex}")
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
            "reason_unavailable": "YouTube video playback is rendered via official YouTube embedded player."
        }
