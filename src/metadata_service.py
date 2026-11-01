import json
import logging
import re
import time
from typing import Optional

from src.db import get_db_connection
from src import http_client
from src.spotify_api import SpotifyApiHelper
from src.spotify_direct import SpotifyDirectClient

logger = logging.getLogger("MetadataService")


# How long a cached resolution is trusted. Playlists change, so they expire quickly; tracks/albums rarely do.
CACHE_TTL_SECONDS = {"playlist": 3600, "artist": 86400, "album": 7 * 86400, "track": 7 * 86400}


class MetadataService:
    """
    Unified metadata resolution engine supporting tracks, playlists,
    albums, and artists with local SQLite caching.
    """
    def __init__(self):
        self.web_api = SpotifyApiHelper()
        self.direct_client = SpotifyDirectClient()

    def resolve_item(self, kind: str, item_id: str, force_refresh: bool = False) -> dict:
        conn = get_db_connection()

        # Check local DB cache unless forced refresh
        if not force_refresh:
            cur = conn.execute("SELECT snapshot_json, created_at FROM sync_snapshots WHERE collection_id = ?", (item_id,))
            row = cur.fetchone()
            ttl = CACHE_TTL_SECONDS.get(kind, 3600)
            if row and (time.time() - (row["created_at"] or 0)) < ttl:
                try:
                    logger.info(f"Loaded {kind} {item_id} from SQLite cache")
                    return json.loads(row["snapshot_json"])
                except Exception:
                    logger.warning("Cached %s %s could not be read; fetching it again", kind, item_id, exc_info=True)

        result = {}
        if kind == "track":
            result = self._resolve_track(item_id)
        elif kind == "playlist":
            result = self._resolve_playlist(item_id)
        elif kind == "album":
            result = self._resolve_album(item_id)
        elif kind == "artist":
            result = self._resolve_artist(item_id)
        else:
            raise ValueError(f"Unsupported resource kind: {kind}")

        # Cache result snapshot into DB
        if result:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sync_snapshots (collection_id, collection_name, track_count, snapshot_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(collection_id) DO UPDATE SET
                        collection_name = excluded.collection_name,
                        track_count = excluded.track_count,
                        snapshot_json = excluded.snapshot_json,
                        created_at = excluded.created_at
                    """,
                    (
                        item_id,
                        result.get("name") or result.get("title", ""),
                        len(result.get("tracks", [])) if "tracks" in result else 1,
                        json.dumps(result),
                        time.time()
                    )
                )

        return result

    @classmethod
    def _scrape_track_web_info(cls, track_id: str) -> dict:
        """
        Scrapes open.spotify.com/track/{track_id} to extract authentic album name,
        release year, track number, and high-resolution artwork directly.
        """
        url = f"https://open.spotify.com/track/{track_id}"
        try:
            html = http_client.get_text(url, headers={"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"}, timeout=8)

            album_name = ""
            year = ""
            cover_url = ""
            track_number = None
            desc_str = ""

            # Linear meta tag parser: immune to whitespace, tag ordering, or regex greediness
            for tag in re.findall(r'<meta\b[^>]*>', html, re.IGNORECASE):
                prop_m = re.search(r'(?:property|name)=["\']([^"\']+)["\']', tag, re.IGNORECASE)
                val_m = re.search(r'content=["\']([^"\']*)["\']', tag, re.IGNORECASE)
                if prop_m and val_m:
                    prop = prop_m.group(1).lower()
                    val = val_m.group(1)
                    if prop == "og:image" and not cover_url:
                        cover_url = val
                    elif prop == "music:release_date" and not year:
                        year = val[:4]
                    elif prop == "music:album:track" and not track_number:
                        try:
                            track_number = int(val)
                        except ValueError:
                            pass
                    elif prop == "og:description":
                        desc_str = val

            if desc_str:
                parts = [p.strip() for p in re.split(r'[\u00b7\u2022]', desc_str) if p.strip()]
                if len(parts) >= 2:
                    album_name = parts[1]
                    if not year:
                        for p in reversed(parts):
                            if re.match(r'^\d{4}$', p):
                                year = p
                                break

            return {
                "album": album_name,
                "year": year,
                "cover_url": cover_url,
                "track_number": track_number
            }
        except Exception as e:
            logger.debug(f"Track web scrape failed for {track_id}: {e}")
            return {}

    @classmethod
    def _scrape_album_web_info(cls, album_id: str) -> dict:
        """
        Scrapes open.spotify.com/album/{album_id} to extract album artist,
        release year, and high-resolution album cover artwork.
        """
        url = f"https://open.spotify.com/album/{album_id}"
        try:
            html = http_client.get_text(url, headers={"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"}, timeout=8)

            cover_url = ""
            album_artist = ""
            year = ""
            desc_str = ""

            for tag in re.findall(r'<meta\b[^>]*>', html, re.IGNORECASE):
                prop_m = re.search(r'(?:property|name)=["\']([^"\']+)["\']', tag, re.IGNORECASE)
                val_m = re.search(r'content=["\']([^"\']*)["\']', tag, re.IGNORECASE)
                if prop_m and val_m:
                    prop = prop_m.group(1).lower()
                    val = val_m.group(1)
                    if prop == "og:image" and not cover_url:
                        cover_url = val
                    elif prop == "og:title" and not album_artist:
                        m_by = re.search(r'Album by ([^|]+)', val)
                        if m_by:
                            album_artist = m_by.group(1).strip()
                    elif prop == "music:release_date" and not year:
                        year = val[:4]
                    elif prop == "og:description":
                        desc_str = val

            if not year or not album_artist:
                if desc_str:
                    parts = [p.strip() for p in re.split(r'[\u00b7\u2022]', desc_str) if p.strip()]
                    if parts and not album_artist:
                        album_artist = parts[0]
                    if not year:
                        for p in parts:
                            if re.match(r'^\d{4}$', p):
                                year = p
                                break

            return {
                "cover_url": cover_url,
                "album_artist": album_artist,
                "year": year
            }
        except Exception as e:
            logger.debug(f"Album web scrape failed for {album_id}: {e}")
            return {}

    @classmethod
    def _resolve_from_embed(cls, kind: str, item_id: str) -> Optional[dict]:
        """
        Public Zero-Key Resolver:
        Extracts official Spotify metadata directly from open.spotify.com/embed
        without requiring Spotify API credentials or Spotify Premium!
        """
        embed_url = f"https://open.spotify.com/embed/{kind}/{item_id}"
        try:
            html = http_client.get_text(embed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}, timeout=12)

            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
            if not m:
                return None
            data = json.loads(m.group(1))
            entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
            if not entity:
                return None

            if kind == "track":
                artists = [a.get("name") for a in entity.get("artists", []) if a.get("name")]
                if not artists:
                    artists = ["Unknown Artist"]
                cover_url = entity.get("coverArt", {}).get("sources", [{}])[0].get("url") or ""
                year_val = entity.get("releaseDate", {}).get("isoString", "")[:4] if entity.get("releaseDate") else ""
                album_val = (entity.get("album") or {}).get("name", "")

                # Enrich authentic album title, year, track number, and cover art if missing from embed payload
                track_number = 1
                if not album_val or not cover_url or not year_val:
                    web_info = cls._scrape_track_web_info(item_id)
                    if web_info:
                        if not album_val and web_info.get("album"):
                            album_val = web_info["album"]
                        if not year_val and web_info.get("year"):
                            year_val = web_info["year"]
                        if not cover_url and web_info.get("cover_url"):
                            cover_url = web_info["cover_url"]
                        if web_info.get("track_number"):
                            track_number = web_info["track_number"]

                return {
                    "kind": "track",
                    "id": item_id,
                    "title": entity.get("name", "Unknown Track"),
                    "artists": artists,
                    "album": album_val or "Singles",
                    "album_artist": artists[0],
                    "year": year_val,
                    "release_date": entity.get("releaseDate", {}).get("isoString", "") if entity.get("releaseDate") else year_val,
                    "track_number": track_number,
                    "total_tracks": 1,
                    "disc_number": 1,
                    "isrc": "",
                    "cover_url": cover_url,
                    "duration_ms": entity.get("duration", 0),
                    "explicit": bool(entity.get("isExplicit", False)),
                    "spotify_url": f"https://open.spotify.com/track/{item_id}"
                }

            elif kind == "album":
                name = entity.get("name", f"Album_{item_id}")
                cover_url = entity.get("coverArt", {}).get("sources", [{}])[0].get("url") or ""
                subtitle = entity.get("subtitle", "Unknown Artist")
                album_artists = [a.strip() for a in subtitle.split(",") if a.strip()] or [subtitle]
                primary_artist = album_artists[0] if album_artists else "Unknown Artist"
                year_val = ""

                # Enrich with high-res artwork, authentic artist name, and release year
                web_info = cls._scrape_album_web_info(item_id)
                if web_info:
                    if web_info.get("cover_url"):
                        cover_url = web_info["cover_url"]
                    if web_info.get("album_artist"):
                        primary_artist = web_info["album_artist"]
                        if primary_artist not in album_artists:
                            album_artists = [primary_artist] + album_artists
                    if web_info.get("year"):
                        year_val = web_info["year"]

                raw_tracks = entity.get("trackList", [])
                tracks = []
                for idx, t in enumerate(raw_tracks, 1):
                    t_uri = t.get("uri", "")
                    t_id = t_uri.replace("spotify:track:", "") or f"track_{idx}"
                    t_title = t.get("title", f"Track {idx}")
                    t_sub = t.get("subtitle", primary_artist)
                    t_artists = [a.strip() for a in t_sub.split(",") if a.strip()] or [t_sub]
                    tracks.append({
                        "id": t_id,
                        "uri": t_uri or f"spotify:track:{t_id}",
                        "title": t_title,
                        "artists": t_artists,
                        "album": name,
                        "album_artist": primary_artist,
                        "position": idx,
                        "track_number": idx,
                        "duration_ms": t.get("duration", 0),
                        "cover_url": cover_url,
                        "year": year_val,
                        "collection_id": item_id,
                        "collection_name": name,
                        "playlist_name": name,
                        "is_album": True
                    })

                return {
                    "kind": "album",
                    "id": item_id,
                    "name": name,
                    "title": name,
                    "artist": primary_artist,
                    "artists": album_artists,
                    "year": year_val,
                    "release_date": year_val,
                    "description": entity.get("description", ""),
                    "owner": primary_artist,
                    "cover_url": cover_url,
                    "total_tracks": len(tracks),
                    "tracks": tracks
                }

            elif kind == "playlist":
                name = entity.get("name", f"Playlist_{item_id}")
                cover_url = entity.get("coverArt", {}).get("sources", [{}])[0].get("url") or ""
                raw_tracks = entity.get("trackList", [])
                tracks = []
                for idx, t in enumerate(raw_tracks, 1):
                    t_uri = t.get("uri", "")
                    t_id = t_uri.replace("spotify:track:", "") or f"track_{idx}"
                    t_title = t.get("title", f"Track {idx}")
                    subtitle = t.get("subtitle", "Unknown Artist")
                    t_artists = [a.strip() for a in subtitle.split(",") if a.strip()] or [subtitle]
                    tracks.append({
                        "id": t_id,
                        "uri": t_uri or f"spotify:track:{t_id}",
                        "title": t_title,
                        "artists": t_artists,
                        "album": "",  # Preserved blank so track-level resolution gets authentic album
                        "album_artist": t_artists[0],
                        "position": idx,
                        "track_number": idx,
                        "duration_ms": t.get("duration", 0),
                        "cover_url": cover_url,
                        "year": "",
                        "collection_id": item_id,
                        "collection_name": name,
                        "playlist_name": name,
                        "is_playlist": True
                    })

                return {
                    "kind": "playlist",
                    "id": item_id,
                    "name": name,
                    "title": name,
                    "description": entity.get("description", ""),
                    "owner": entity.get("owner", {}).get("name", "Spotify"),
                    "cover_url": cover_url,
                    "total_tracks": len(tracks),
                    "tracks": tracks
                }
            return None   # a kind this resolver does not handle (e.g. artist)
        except Exception as e:
            logger.warning(f"Public embed metadata resolution failed for {kind} {item_id}: {e}")
            return None

    def _resolve_track(self, track_id: str) -> dict:
        # 1. Try Web API first if available
        if self.web_api.has_web_api():
            try:
                t = self.web_api.client().track(track_id)
                artists = [a["name"] for a in t.get("artists", [])]
                album = t.get("album", {})
                images = album.get("images", [])
                cover_url = images[0]["url"] if images else None

                return {
                    "kind": "track",
                    "id": track_id,
                    "title": t["name"],
                    "artists": artists,
                    "album": album.get("name", ""),
                    "album_artist": album.get("artists", [{}])[0].get("name", ""),
                    "year": album.get("release_date", "")[:4] if album.get("release_date") else "",
                    "release_date": album.get("release_date", ""),
                    "track_number": t.get("track_number", 1),
                    "total_tracks": album.get("total_tracks", 1),
                    "disc_number": t.get("disc_number", 1),
                    "isrc": t.get("external_ids", {}).get("isrc", ""),
                    "cover_url": cover_url,
                    "duration_ms": t.get("duration_ms", 0),
                    "explicit": t.get("explicit", False),
                    "spotify_url": t.get("external_urls", {}).get("spotify", f"https://open.spotify.com/track/{track_id}")
                }
            except Exception as e:
                logger.warning(f"Web API track fetch failed: {e}. Falling back to public embed.")

        # 2. Try Public Zero-Key Embed Resolver
        embed_track = self._resolve_from_embed("track", track_id)
        if embed_track:
            return embed_track

        # 3. Fallback to direct client
        try:
            meta = self.direct_client.get_track_metadata(track_id)  # metadata only: never rip audio just to read tags
            return {
                "kind": "track",
                "id": track_id,
                "title": meta.get("title", ""),
                "artists": meta.get("artists", []),
                "album": meta.get("album", ""),
                "album_artist": meta.get("artists", [""])[0],
                "year": meta.get("year", ""),
                "release_date": str(meta.get("year", "")),
                "track_number": meta.get("track_number", 1),
                "total_tracks": meta.get("total_tracks", 1),
                "disc_number": meta.get("disc_number", 1),
                "isrc": meta.get("isrc", ""),
                "cover_url": meta.get("cover_url", ""),
                "duration_ms": meta.get("duration_ms", 0),
                "explicit": False,
                "spotify_url": f"https://open.spotify.com/track/{track_id}"
            }
        except Exception as e:
            raise RuntimeError(f"Could not resolve track {track_id}: {e}") from e

    def _resolve_playlist(self, playlist_id: str) -> dict:
        # 1. Try Web API first if available
        if self.web_api.has_web_api():
            try:
                name, tracks = self.web_api.get_playlist_metadata(playlist_id)
                for t in tracks:
                    t["collection_id"] = playlist_id
                    t["collection_name"] = name
                    t["playlist_name"] = name
                pl = self.web_api.client().playlist(playlist_id, fields="name,description,owner,images")
                images = pl.get("images", [])
                cover_url = images[0]["url"] if images else None

                return {
                    "kind": "playlist",
                    "id": playlist_id,
                    "name": name,
                    "description": pl.get("description", ""),
                    "owner": pl.get("owner", {}).get("display_name", "Spotify User"),
                    "cover_url": cover_url,
                    "total_tracks": len(tracks),
                    "tracks": tracks
                }
            except Exception as e:
                logger.warning(f"Web API playlist resolution failed: {e}. Falling back to public embed.")

        # 2. Try Public Zero-Key Embed Resolver
        embed_pl = self._resolve_from_embed("playlist", playlist_id)
        if embed_pl and embed_pl.get("tracks"):
            return embed_pl

        # 3. Fallback to direct client AP
        name, uris = self.direct_client.extract_playlist_info(playlist_id)
        tracks = []
        for idx, u in enumerate(uris, 1):
            t_id = u.replace("spotify:track:", "")
            tracks.append({
                "id": t_id,
                "uri": u,
                "title": f"Track {idx} ({t_id})",
                "artists": ["Spotify Track"],
                "album": name,
                "position": idx,
                "collection_id": playlist_id,
                "collection_name": name,
                "playlist_name": name
            })

        return {
            "kind": "playlist",
            "id": playlist_id,
            "name": name,
            "description": "",
            "owner": "Spotify",
            "cover_url": "",
            "total_tracks": len(tracks),
            "tracks": tracks
        }

    def _resolve_album(self, album_id: str) -> dict:
        # 1. Try Web API first if available
        if self.web_api.has_web_api():
            try:
                alb = self.web_api.client().album(album_id)
                images = alb.get("images", [])
                cover_url = images[0]["url"] if images else None
                album_name = alb.get("name", "")
                artists = [a["name"] for a in alb.get("artists", [])]
                primary_artist = artists[0] if artists else "Unknown"
                release_date = alb.get("release_date", "")

                tracks = []
                results = alb.get("tracks", {})
                while results:
                    for t in results.get("items", []):
                        tracks.append({
                            "id": t["id"],
                            "uri": t["uri"],
                            "title": t["name"],
                            "artists": [a["name"] for a in t.get("artists", [])],
                            "album": album_name,
                            "album_artist": primary_artist,
                            "track_number": t.get("track_number", 1),
                            "disc_number": t.get("disc_number", 1),
                            "duration_ms": t.get("duration_ms", 0),
                            "explicit": t.get("explicit", False),
                            "year": release_date[:4] if release_date else "",
                            "cover_url": cover_url
                        })
                    if results.get("next"):
                        results = self.web_api.client().next(results)
                    else:
                        break

                disc_count = max([t["disc_number"] for t in tracks], default=1)

                return {
                    "kind": "album",
                    "id": album_id,
                    "name": album_name,
                    "album_type": alb.get("album_type", "album"),
                    "artist": primary_artist,
                    "artists": artists,
                    "release_date": release_date,
                    "year": release_date[:4] if release_date else "",
                    "cover_url": cover_url,
                    "total_tracks": len(tracks),
                    "total_discs": disc_count,
                    "tracks": tracks
                }
            except Exception as e:
                logger.warning(f"Web API album resolution failed: {e}. Falling back to public embed.")

        # 2. Try Public Zero-Key Embed Resolver
        embed_alb = self._resolve_from_embed("album", album_id)
        if embed_alb and embed_alb.get("tracks"):
            return embed_alb

        raise RuntimeError(f"Could not resolve album {album_id} via public embed or Web API.")

    def _resolve_artist(self, artist_id: str) -> dict:
        if self.web_api.has_web_api():
            artist_obj = self.web_api.client().artist(artist_id)
            images = artist_obj.get("images", [])
            image_url = images[0]["url"] if images else None

            # Fetch top tracks
            top = self.web_api.client().artist_top_tracks(artist_id)
            top_tracks = []
            for t in top.get("tracks", []):
                top_tracks.append({
                    "id": t["id"],
                    "uri": t["uri"],
                    "title": t["name"],
                    "artists": [a["name"] for a in t.get("artists", [])],
                    "album": t.get("album", {}).get("name", ""),
                    "cover_url": t.get("album", {}).get("images", [{}])[0].get("url", ""),
                    "duration_ms": t.get("duration_ms", 0)
                })

            # Fetch albums
            albums_res = self.web_api.client().artist_albums(artist_id, album_type="album,single", limit=20)
            albums = []
            for a in albums_res.get("items", []):
                albums.append({
                    "id": a["id"],
                    "name": a["name"],
                    "type": a.get("album_type", "album"),
                    "release_date": a.get("release_date", ""),
                    "cover_url": a.get("images", [{}])[0].get("url", "") if a.get("images") else ""
                })

            return {
                "kind": "artist",
                "id": artist_id,
                "name": artist_obj.get("name", ""),
                "genres": artist_obj.get("genres", []),
                "followers": artist_obj.get("followers", {}).get("total", 0),
                "cover_url": image_url,
                "top_tracks": top_tracks,
                "albums": albums
            }

        raise RuntimeError("Spotify Web API credentials required to resolve artist discographies.")
