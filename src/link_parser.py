import csv
import io
import re
from typing import Dict, List


class ParsedLink:
    def __init__(
        self,
        raw: str,
        kind: str,
        item_id: str,
        clean_url: str,
        is_valid: bool = True,
        error: str = "",
        provider: str = "spotify"
    ):
        self.raw = raw
        self.kind = kind  # track, album, playlist, artist, video, search, unsupported
        self.item_id = item_id
        self.clean_url = clean_url
        self.is_valid = is_valid
        self.error = error
        self.provider = provider  # spotify, ytmusic, youtube, search, unknown

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "provider": self.provider,
            "kind": self.kind,
            "id": self.item_id,
            "clean_url": self.clean_url,
            "is_valid": self.is_valid,
            "error": self.error
        }


class LinkParser:
    """
    Exhaustive multi-source link and input parser supporting Spotify,
    YouTube Music, and YouTube URLs, URIs, search queries, multiline inputs, and file imports.
    """

    URI_REGEX = re.compile(r"^spotify:(track|album|playlist|artist):([a-zA-Z0-9]{22})", re.IGNORECASE)
    URL_REGEX = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/([a-zA-Z0-9]{22})", re.IGNORECASE)
    SHORT_REGEX = re.compile(r"spotify\.link/([a-zA-Z0-9]+)", re.IGNORECASE)

    YTM_TRACK_REGEX = re.compile(r"music\.youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})", re.IGNORECASE)
    YTM_PLAYLIST_REGEX = re.compile(r"music\.youtube\.com/playlist\?.*list=([a-zA-Z0-9_-]+)", re.IGNORECASE)
    YTM_BROWSE_REGEX = re.compile(r"music\.youtube\.com/browse/([a-zA-Z0-9_-]+)", re.IGNORECASE)

    YT_VIDEO_REGEX = re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/)([a-zA-Z0-9_-]{11})", re.IGNORECASE)
    YT_PLAYLIST_REGEX = re.compile(r"youtube\.com/playlist\?.*list=([a-zA-Z0-9_-]+)", re.IGNORECASE)
    YT_CHANNEL_REGEX = re.compile(r"youtube\.com/(?:channel/|c/|@)([a-zA-Z0-9_.-]+)", re.IGNORECASE)

    @classmethod
    def resolve_shortlink(cls, url: str, timeout: float = 5.0) -> ParsedLink:
        """
        Follows HTTP redirects for spotify.link URLs to resolve to the target Spotify URI/URL.
        """
        from src import http_client
        headers: Dict[str, str] = {}
        try:
            resp = http_client.head(url, allow_redirects=True, timeout=timeout, headers=headers, retries=1)
            target_url = resp.url
            if "open.spotify.com" in target_url or "spotify:" in target_url:
                parsed = cls.parse_single(target_url, resolve_redirects=False)
                parsed.raw = url
                return parsed
            resp = http_client.get(url, allow_redirects=True, stream=True, timeout=timeout, headers=headers, retries=1)
            target_url = resp.url
            parsed = cls.parse_single(target_url, resolve_redirects=False)
            parsed.raw = url
            return parsed
        except Exception as e:
            return ParsedLink(url, "shortlink", "", url, False, f"Failed to resolve shortlink: {e}", "spotify")

    @classmethod
    def parse_url(cls, text: str, resolve_redirects: bool = False) -> ParsedLink:
        """Alias for parse_single."""
        return cls.parse_single(text, resolve_redirects=resolve_redirects)

    @classmethod
    def parse_single(cls, text: str, resolve_redirects: bool = False) -> ParsedLink:
        raw = text.strip()
        if not raw:
            return ParsedLink(raw, "empty", "", "", False, "Empty input", "unknown")

        # 1. Match Spotify URI (spotify:track:...)
        uri_m = cls.URI_REGEX.match(raw)
        if uri_m:
            kind, item_id = uri_m.group(1).lower(), uri_m.group(2)
            clean_url = f"https://open.spotify.com/{kind}/{item_id}"
            return ParsedLink(raw, kind, item_id, clean_url, True, "", "spotify")

        # 2. Match Spotify Web URL
        url_m = cls.URL_REGEX.search(raw)
        if url_m:
            kind, item_id = url_m.group(1).lower(), url_m.group(2)
            clean_url = f"https://open.spotify.com/{kind}/{item_id}"
            return ParsedLink(raw, kind, item_id, clean_url, True, "", "spotify")

        # 3. Match Spotify Shortened Link (spotify.link/...)
        short_m = cls.SHORT_REGEX.search(raw)
        if short_m:
            if resolve_redirects:
                return cls.resolve_shortlink(raw)
            short_id = short_m.group(1)
            return ParsedLink(raw, "shortlink", short_id, raw, True, "Requires redirect expansion", "spotify")

        # 4. Match YouTube Music URLs
        if "music.youtube.com" in raw:
            ytm_tr = cls.YTM_TRACK_REGEX.search(raw)
            if ytm_tr:
                item_id = ytm_tr.group(1)
                return ParsedLink(raw, "track", item_id, f"https://music.youtube.com/watch?v={item_id}", True, "", "ytmusic")

            ytm_pl = cls.YTM_PLAYLIST_REGEX.search(raw)
            if ytm_pl:
                item_id = ytm_pl.group(1)
                kind = "album" if item_id.startswith("OLAK5uy_") else "playlist"
                return ParsedLink(raw, kind, item_id, f"https://music.youtube.com/playlist?list={item_id}", True, "", "ytmusic")

            ytm_br = cls.YTM_BROWSE_REGEX.search(raw)
            if ytm_br:
                item_id = ytm_br.group(1)
                kind = "album" if item_id.startswith("MPRE") else "artist"
                return ParsedLink(raw, kind, item_id, f"https://music.youtube.com/browse/{item_id}", True, "", "ytmusic")

        # 5. Match YouTube URLs
        if "youtube.com" in raw or "youtu.be" in raw:
            yt_v = cls.YT_VIDEO_REGEX.search(raw)
            if yt_v:
                item_id = yt_v.group(1)
                return ParsedLink(raw, "video", item_id, f"https://www.youtube.com/watch?v={item_id}", True, "", "youtube")

            yt_pl = cls.YT_PLAYLIST_REGEX.search(raw)
            if yt_pl:
                item_id = yt_pl.group(1)
                return ParsedLink(raw, "playlist", item_id, f"https://www.youtube.com/playlist?list={item_id}", True, "", "youtube")

            yt_ch = cls.YT_CHANNEL_REGEX.search(raw)
            if yt_ch:
                item_id = yt_ch.group(1)
                return ParsedLink(raw, "channel", item_id, raw, True, "", "youtube")

        # 6. Raw 22-char base62 Spotify ID check
        if re.match(r"^[a-zA-Z0-9]{22}$", raw):
            return ParsedLink(raw, "track", raw, f"https://open.spotify.com/track/{raw}", True, "", "spotify")

        # 7. Raw 11-char YouTube ID check
        if re.match(r"^[a-zA-Z0-9_-]{11}$", raw):
            return ParsedLink(raw, "video", raw, f"https://www.youtube.com/watch?v={raw}", True, "", "youtube")

        # 8. Search query fallback (title + artist)
        if len(raw) > 2 and not raw.startswith("http"):
            return ParsedLink(raw, "search", raw, "", True, "", "search")

        return ParsedLink(raw, "unsupported", "", "", False, "Unsupported or malformed URL", "unknown")

    @classmethod
    def parse_multiline(cls, text: str, deduplicate: bool = True, resolve_redirects: bool = False) -> List[ParsedLink]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # Also split by commas if user pasted comma-separated links
        candidates = []
        for line in lines:
            if "," in line and not line.startswith("http"):
                candidates.extend([p.strip() for p in line.split(",") if p.strip()])
            else:
                candidates.append(line)

        seen_keys = set()
        results = []

        for item in candidates:
            parsed = cls.parse_single(item, resolve_redirects=resolve_redirects)
            key = f"{parsed.kind}:{parsed.item_id}" if parsed.is_valid and parsed.item_id else parsed.raw
            if deduplicate and key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(parsed)

        return results

    @classmethod
    def parse_file_content(cls, content: str, filename: str = "", resolve_redirects: bool = False) -> List[ParsedLink]:
        if filename.lower().endswith(".csv") or "," in content:
            # CSV parsing
            reader = csv.reader(io.StringIO(content))
            extracted_urls = []
            for row in reader:
                for col in row:
                    val = col.strip()
                    if "spotify.com" in val or "spotify:" in val or (len(val) == 22 and val.isalnum()):
                        extracted_urls.append(val)
            if extracted_urls:
                return cls.parse_multiline("\n".join(extracted_urls), resolve_redirects=resolve_redirects)

        # Plain text
        return cls.parse_multiline(content, resolve_redirects=resolve_redirects)
