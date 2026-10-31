"""
MCP (Model Context Protocol) server for Spotify Downloader Studio.
Enables ChatGPT, Claude, and external AI agents to:
1. Search music across Spotify, YouTube Music, YouTube, and local library.
2. Resolve metadata for Spotify, YouTube Music, and YouTube URLs.
3. Discover and inspect conservative cross-source alternatives (Rules A-D).
4. Inspect queue status, jobs, and library statistics.
5. Retrieve playback and download capabilities per provider.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.mcpserver import MCPServer
from src.db import get_db_connection
from src.providers.base import NormalizedItem
from src.source_resolution import get_source_resolution_service
from src.unified_search import get_unified_search_service
from src.playback_capability import PlaybackCapabilityService

# Initialize MCP Server
mcp = MCPServer(
    "spotify-downloader-studio",
    instructions="MCP Server providing multi-source music search, metadata resolution, library inspection, and cross-source alternative discovery."
)


@mcp.tool()
def search_music(
    query: str,
    scope: str = "all",
    category: str = "all",
    limit: int = 10
) -> Dict[str, Any]:
    """
    Search music catalogue across Spotify, YouTube Music, YouTube, and local library.

    Args:
        query: Search term (e.g. song title, artist name, album name).
        scope: Provider scope - 'all', 'spotify', 'ytmusic', 'youtube', or 'local'.
        category: Item category - 'all', 'tracks', 'albums', 'artists', 'playlists', or 'videos'.
        limit: Maximum results per provider (1-25).
    """
    search_service = get_unified_search_service()
    res = search_service.search(
        query=query,
        scope=scope,
        entity_types=[category] if category != "all" else None,
        limit_per_provider=max(1, min(limit, 25))
    )
    # Sanitize local paths in results to prevent host reconnaissance
    raw_results = res.get("results", [])
    sanitized_results = []
    for item in raw_results:
        if item.get("provider") == "local":
            it = dict(item)
            p_id = it.get("provider_item_id", "")
            it["provider_item_id"] = Path(p_id).name if p_id else ""
            it["canonical_url"] = f"PROJECT_ROOT/downloads/{Path(it.get('canonical_url', '')).name}"
            if "capabilities" in it and isinstance(it["capabilities"], dict):
                caps = dict(it["capabilities"])
                caps.pop("stream_url", None)
                it["capabilities"] = caps
            sanitized_results.append(it)
        else:
            sanitized_results.append(item)

    return {
        "query": res.get("query"),
        "scope": res.get("scope"),
        "total_results": len(sanitized_results),
        "results": sanitized_results,
        "provider_statuses": res.get("provider_statuses", {})
    }


@mcp.tool()
def resolve_music_url(url: str) -> Dict[str, Any]:
    """
    Resolve authentic metadata for any Spotify, YouTube Music, or YouTube URL.
    Adheres strictly to Rule A: Spotify URLs are authoritative and never silently substituted.

    Args:
        url: Full web URL (e.g. https://open.spotify.com/track/..., https://music.youtube.com/watch?v=..., https://www.youtube.com/watch?v=...).
    """
    res_service = get_source_resolution_service()
    return res_service.resolve_url(url)


@mcp.tool()
def find_cross_source_alternatives(
    title: str,
    artist: str,
    spotify_id: str = "",
    duration_ms: Optional[int] = None,
    isrc: Optional[str] = None
) -> Dict[str, Any]:
    """
    Discover conservative, non-destructive cross-source alternatives on YouTube Music and YouTube
    for a given track (Rule B / Rule C). Explains variance (studio vs live, remix, acoustic) and match confidence.

    Args:
        title: Track title.
        artist: Primary artist or composer name.
        spotify_id: Spotify 22-character ID (if known).
        duration_ms: Track duration in milliseconds (for duration delta comparison).
        isrc: International Standard Recording Code (if known).
    """
    res_service = get_source_resolution_service()
    item = NormalizedItem(
        provider="spotify",
        provider_item_id=spotify_id or "query",
        entity_type="track",
        title=title,
        primary_creator=artist,
        duration_ms=duration_ms,
        isrc=isrc
    )
    return res_service.find_alternatives_for_track(item)


@mcp.tool()
def get_library_summary() -> Dict[str, Any]:
    """
    Retrieve statistics and summary of the locally indexed music library on D: drive.
    """
    conn = get_db_connection()
    total_tracks = conn.execute("SELECT COUNT(*) as count FROM library_files").fetchone()["count"]
    total_artists = conn.execute("SELECT COUNT(DISTINCT artist) as count FROM library_files WHERE artist != ''").fetchone()["count"]
    total_albums = conn.execute("SELECT COUNT(DISTINCT album) as count FROM library_files WHERE album != ''").fetchone()["count"]

    # Recent 5 tracks (paths redacted for privacy/security)
    recent = conn.execute(
        "SELECT title, artist, album, duration_sec, filename FROM library_files ORDER BY mtime DESC LIMIT 5"
    ).fetchall()

    return {
        "downloads_location": "PROJECT_ROOT/downloads",
        "total_tracks": total_tracks,
        "total_artists": total_artists,
        "total_albums": total_albums,
        "recently_added": [
            {
                "title": r["title"] or r["filename"],
                "artist": r["artist"] or "Unknown",
                "album": r["album"] or "Unknown",
                "duration_sec": r["duration_sec"]
            }
            for r in recent
        ]
    }


@mcp.tool()
def get_queue_status() -> Dict[str, Any]:
    """
    Inspect the current download queue status, concurrency, and jobs.
    """
    conn = get_db_connection()
    jobs = conn.execute(
        "SELECT id, track_id, title, artists, album, provider, stage, status, progress, error FROM jobs ORDER BY added_at DESC LIMIT 20"
    ).fetchall()

    counts = {
        "total": len(jobs),
        "queued": sum(1 for j in jobs if j["status"] == "QUEUED"),
        "active": sum(1 for j in jobs if j["status"] in ("ACTIVE", "DOWNLOADING", "CONVERTING")),
        "completed": sum(1 for j in jobs if j["status"] == "COMPLETED"),
        "failed": sum(1 for j in jobs if j["status"] == "FAILED")
    }

    return {
        "counts": counts,
        "recent_jobs": [dict(j) for j in jobs]
    }


@mcp.tool()
def get_provider_capabilities() -> Dict[str, Any]:
    """
    Returns capability matrices, licensing disclosures, and stream permissions for all supported providers.
    """
    return PlaybackCapabilityService.get_all_provider_statuses()


# Server entrypoint
def main():
    mcp.run()


if __name__ == "__main__":
    main()
