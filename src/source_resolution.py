import json
import logging
import time
from typing import Any, Dict, List, Optional, Union

from src.cross_source_matcher import CrossSourceMatchingService
from src.db import get_db_connection
from src.link_parser import LinkParser
from src.providers import (
    SpotifyProvider,
    YouTubeMusicProvider,
    YouTubeProvider,
    NormalizedItem,
    ProviderName
)

logger = logging.getLogger("SourceResolutionService")


class SourceResolutionService:
    """
    Enforces the Core Source-Selection Contract (Rules A, B, C, D).
    Guarantees:
    - Spotify URLs remain Spotify-sourced by default with zero silent substitution.
    - Other sources are treated as explicit, visibly labelled alternatives.
    - User intent outranks automated matching.
    """

    def __init__(self):
        self.spotify_provider = SpotifyProvider()
        self.ytmusic_provider = YouTubeMusicProvider()
        self.youtube_provider = YouTubeProvider()
        self.matcher = CrossSourceMatchingService()

    def resolve_url(self, url: str) -> Dict[str, Any]:
        """
        Ingests and resolves any supported music URL according to Rules A, B, and C.
        Returns normalized resource dictionary.
        """
        raw = url.strip()
        parsed = LinkParser.parse_single(raw)

        # 1. Spotify URL Handling (Rule A)
        if "spotify.com" in raw or raw.startswith("spotify:"):
            if not parsed.is_valid:
                return {
                    "success": False,
                    "provider": ProviderName.SPOTIFY.value,
                    "error": f"Invalid Spotify URL or entity ID: {parsed.error}",
                    "data": None
                }

            try:
                resolved_item = self.spotify_provider.resolve_url(raw)
                if not resolved_item:
                    return {
                        "success": False,
                        "provider": ProviderName.SPOTIFY.value,
                        "error": f"Spotify entity '{parsed.item_id}' ({parsed.kind}) could not be resolved from Spotify catalogue.",
                        "data": None
                    }
                return {
                    "success": True,
                    "provider": ProviderName.SPOTIFY.value,
                    "kind": parsed.kind,
                    "data": resolved_item.to_dict()
                }
            except Exception as e:
                logger.error(f"Spotify resolution failed: {e}")
                # RULE A: Do NOT silently fall back to YouTube! Report actual failure.
                return {
                    "success": False,
                    "provider": ProviderName.SPOTIFY.value,
                    "error": f"Spotify service resolution failed: {str(e)}. (Silent YouTube fallback prevented by policy)",
                    "data": None
                }

        # 2. YouTube Music URL Handling (Rule B)
        elif "music.youtube.com" in raw:
            resolved_item = self.ytmusic_provider.resolve_url(raw)
            if not resolved_item:
                return {
                    "success": False,
                    "provider": ProviderName.YTMUSIC.value,
                    "error": "Could not resolve YouTube Music item from provided URL.",
                    "data": None
                }
            return {
                "success": True,
                "provider": ProviderName.YTMUSIC.value,
                "kind": resolved_item.entity_type,
                "data": resolved_item.to_dict()
            }

        # 3. YouTube URL Handling (Rule B)
        elif "youtube.com" in raw or "youtu.be" in raw:
            resolved_item = self.youtube_provider.resolve_url(raw)
            if not resolved_item:
                return {
                    "success": False,
                    "provider": ProviderName.YOUTUBE.value,
                    "error": "Could not resolve YouTube item from provided URL.",
                    "data": None
                }
            return {
                "success": True,
                "provider": ProviderName.YOUTUBE.value,
                "kind": resolved_item.entity_type,
                "data": resolved_item.to_dict()
            }

        else:
            return {
                "success": False,
                "provider": "unknown",
                "error": "Unsupported URL format. Paste a valid Spotify, YouTube Music, or YouTube link.",
                "data": None
            }

    def find_alternatives_for_track(
        self,
        track_info: Union[dict, NormalizedItem],
        limit_per_source: int = 5
    ) -> Dict[str, Any]:
        """
        Discovers candidate alternatives from YouTube Music and YouTube for a given track.
        Evaluates and ranks all candidates conservatively without auto-selecting.
        """
        # Build normalized target
        if isinstance(track_info, NormalizedItem):
            target = track_info
            primary = target.primary_creator
        else:
            artists = track_info.get("artists") or [track_info.get("artist", "Unknown Artist")]
            primary = artists[0] if isinstance(artists, list) and artists else str(artists)
            target = NormalizedItem(
                provider=track_info.get("provider", "spotify"),
                provider_item_id=track_info.get("id") or track_info.get("track_id", ""),
                entity_type="track",
                title=track_info.get("title", ""),
                primary_creator=primary,
                contributors=artists[1:] if isinstance(artists, list) and len(artists) > 1 else [],
                album=track_info.get("album"),
                duration_ms=track_info.get("duration_ms"),
                isrc=track_info.get("isrc")
            )

        search_query = f"{primary} {target.title}".strip()
        candidates: List[NormalizedItem] = []

        # 1. Search YouTube Music if enabled
        if self.ytmusic_provider.is_enabled():
            try:
                ytm_res = self.ytmusic_provider.search(search_query, limit=limit_per_source)
                candidates.extend(ytm_res.get("tracks", []))
            except Exception as e:
                logger.warning(f"Alternative search on YouTube Music failed: {e}")

        # 2. Search YouTube if enabled
        if self.youtube_provider.is_enabled():
            try:
                yt_res = self.youtube_provider.search(search_query, limit=limit_per_source)
                candidates.extend(yt_res.get("videos", []))
            except Exception as e:
                logger.warning(f"Alternative search on YouTube failed: {e}")

        # 3. Evaluate and rank candidates
        ranked = self.matcher.rank_candidates(target, candidates)

        # Check if user already explicitly selected an alternative previously
        associated = self.get_associated_alternative(target.provider_item_id)

        return {
            "target": target.to_dict(),
            "search_query": search_query,
            "candidates_count": len(ranked),
            "associated_alternative": associated,
            "candidates": ranked
        }

    def associate_alternative(
        self,
        source_item_id: str,
        source_provider: str,
        selected_candidate: dict
    ) -> bool:
        """
        Rule D: Explicit user confirmation required. Associates user-selected alternative.
        Persists into cross_source_matches table.
        """
        try:
            conn = get_db_connection()
            match_id = f"csm_{source_provider}_{source_item_id}_{int(time.time()*1000)}"
            target_prov = selected_candidate.get("provider", "ytmusic")
            target_item_id = selected_candidate.get("provider_item_id") or selected_candidate.get("id", "")

            with conn:
                conn.execute(
                    """
                    INSERT INTO cross_source_matches (
                        id, source_provider, source_item_id, target_provider, target_item_id,
                        match_status, confidence_score, rationale, evidence_json, is_user_selected, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'USER_SELECTED', 1.0, 'Explicitly chosen by user', ?, 1, ?)
                    """,
                    (
                        match_id,
                        source_provider,
                        source_item_id,
                        target_prov,
                        target_item_id,
                        json.dumps(selected_candidate),
                        time.time()
                    )
                )
            logger.info(f"Associated alternative {target_prov}:{target_item_id} with {source_provider}:{source_item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to associate alternative: {e}")
            return False

    def get_associated_alternative(self, source_item_id: str, source_provider: Optional[str] = None) -> Optional[dict]:
        """
        Retrieves user-selected alternative if one exists in database.
        """
        try:
            conn = get_db_connection()
            sql = "SELECT * FROM cross_source_matches WHERE source_item_id = ? AND is_user_selected = 1"
            params = [source_item_id]
            if source_provider:
                sql += " AND source_provider = ?"
                params.append(source_provider)
            cur = conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", params)
            row = cur.fetchone()
            if row:
                evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else {}
                return {
                    "match_id": row["id"],
                    "source_provider": row["source_provider"],
                    "source_item_id": row["source_item_id"],
                    "target_provider": row["target_provider"],
                    "target_item_id": row["target_item_id"],
                    # names the UI reads
                    "alternative_provider": row["target_provider"],
                    "alternative_item_id": row["target_item_id"],
                    "confidence_score": row["confidence_score"],
                    "candidate": evidence,
                    "created_at": row["created_at"]
                }
            return None
        except Exception:
            return None

    def clear_associated_alternative(self, source_item_id: str, source_provider: Optional[str] = None) -> int:
        """Removes every user-selected association for an item. Returns how many were removed."""
        conn = get_db_connection()
        sql = "DELETE FROM cross_source_matches WHERE source_item_id = ? AND is_user_selected = 1"
        params = [source_item_id]
        if source_provider:
            sql += " AND source_provider = ?"
            params.append(source_provider)
        with conn:
            return conn.execute(sql, params).rowcount


_res_instance: Optional[SourceResolutionService] = None

def get_source_resolution_service() -> SourceResolutionService:
    global _res_instance
    if _res_instance is None:
        _res_instance = SourceResolutionService()
    return _res_instance

