import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from src.providers import (
    SpotifyProvider,
    YouTubeMusicProvider,
    YouTubeProvider,
    NormalizedItem,
    ProviderName
)

logger = logging.getLogger("UnifiedSearchService")


class UnifiedSearchService:
    """
    Unified multi-source catalogue search engine.
    Orchestrates searches across Spotify, YouTube Music, and YouTube.
    Isolates provider outages, preserves distinct recordings, and retains full provenance.
    """

    def __init__(self):
        self.spotify = SpotifyProvider()
        self.ytmusic = YouTubeMusicProvider()
        self.youtube = YouTubeProvider()
        self._providers = {
            ProviderName.SPOTIFY.value: self.spotify,
            ProviderName.YTMUSIC.value: self.ytmusic,
            ProviderName.YOUTUBE.value: self.youtube,
        }

    def search(
        self,
        query: str,
        scope: str = "all",
        entity_types: Optional[List[str]] = None,
        limit_per_provider: int = 10,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Executes search across requested provider scope ('all', 'spotify', 'ytmusic', 'youtube').
        Returns structured results by category with provider status metrics.
        """
        # Accept kwargs like entity_type and limit_per_source
        if "entity_type" in kwargs and not entity_types:
            et = kwargs["entity_type"]
            entity_types = [et] if isinstance(et, str) and et != "all" else None
        if "limit_per_source" in kwargs:
            limit_per_provider = kwargs["limit_per_source"]
        if "limit" in kwargs:
            limit_per_provider = kwargs["limit"]

        t0 = time.time()
        q = query.strip()
        if not q:
            return {
                "query": "",
                "scope": scope,
                "elapsed_ms": 0,
                "categories": {
                    "tracks": [],
                    "albums": [],
                    "artists": [],
                    "playlists": [],
                    "videos": []
                },
                "provider_statuses": {}
            }

        target_providers = []
        if scope == "all":
            target_providers = [p for p in self._providers.values() if p.is_enabled()]
        elif scope in self._providers:
            provider_inst = self._providers[scope]
            if provider_inst.is_enabled():
                target_providers = [provider_inst]

        aggregated_categories: Dict[str, List[dict]] = {
            "tracks": [],
            "albums": [],
            "artists": [],
            "playlists": [],
            "videos": []
        }
        provider_statuses: Dict[str, dict] = {}

        def _search_single_provider(p):
            p_name = p.provider_name
            try:
                p_t0 = time.time()
                res = p.search(q, entity_types=entity_types, limit=limit_per_provider)
                elapsed = round((time.time() - p_t0) * 1000, 1)
                total_found = sum(len(items) for items in res.values())
                status = {
                    "status": "SUCCESS" if total_found > 0 else "NO_RESULTS",
                    "count": total_found,
                    "elapsed_ms": elapsed,
                    "error": None
                }
                return p_name, res, status
            except Exception as ex:
                logger.error(f"Provider {p_name} failed during search: {ex}")
                status = {
                    "status": "ERROR",
                    "count": 0,
                    "elapsed_ms": 0,
                    "error": str(ex)
                }
                return p_name, {}, status

        if target_providers:
            with ThreadPoolExecutor(max_workers=min(len(target_providers), 4)) as executor:
                futures = [executor.submit(_search_single_provider, p) for p in target_providers]
                for f in futures:
                    p_name, res, status = f.result()
                    provider_statuses[p_name] = status
                    for cat, items in res.items():
                        if cat in aggregated_categories:
                            for it in items:
                                aggregated_categories[cat].append(it.to_dict())

        # Check local library database if scope is 'all' or 'local'
        if scope in ("all", "local", ProviderName.LOCAL.value):
            try:
                from src.db import get_db_connection
                conn = get_db_connection()
                pat = f"%{q}%"
                local_rows = conn.execute(
                    """SELECT path, filename, title, artist, album, duration_sec, has_artwork
                       FROM library_files
                       WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
                       LIMIT 5""",
                    (pat, pat, pat)
                ).fetchall()
                for r in local_rows:
                    local_item = NormalizedItem(
                        provider=ProviderName.LOCAL.value,
                        provider_item_id=r["path"],
                        entity_type="track",
                        title=r["title"] or r["filename"],
                        primary_creator=r["artist"] or "Local Library",
                        album=r["album"] or "Local",
                        duration_ms=int((r["duration_sec"] or 0) * 1000),
                        canonical_url=r["path"],
                        capabilities={
                            "can_play": True,
                            "can_embed": False,
                            "can_queue": True,
                            "playback_type": "html5_audio",
                            "stream_url": f"/api/player/stream?path={urllib_quote(r['path'])}"
                        }
                    )
                    aggregated_categories["tracks"].insert(0, local_item.to_dict())
            except Exception:
                logger.warning("Local library search failed; results will not include local tracks", exc_info=True)

        # The capability resolver is the single source of truth for what each item can really do
        # (play / embed / queue / download); it overrides whatever a provider guessed inline.
        from src.playback_capability import PlaybackCapabilityService
        for cat_list in aggregated_categories.values():
            for item_dict in cat_list:
                try:
                    resolved = PlaybackCapabilityService.resolve_capabilities(NormalizedItem.from_dict(item_dict))
                    item_dict["capabilities"] = {**(item_dict.get("capabilities") or {}), **resolved}
                except Exception:
                    logger.debug("Could not resolve capabilities for %s", item_dict.get("title"), exc_info=True)

        # Flatten all category results for convenience
        flattened_results = []
        for cat_list in aggregated_categories.values():
            flattened_results.extend(cat_list)

        return {
            "query": q,
            "scope": scope,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "results": flattened_results,
            "categories": aggregated_categories,
            "provider_statuses": provider_statuses
        }


def urllib_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)


_search_instance: Optional[UnifiedSearchService] = None

def get_unified_search_service() -> UnifiedSearchService:
    global _search_instance
    if _search_instance is None:
        _search_instance = UnifiedSearchService()
    return _search_instance

