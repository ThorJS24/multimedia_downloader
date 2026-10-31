import logging
import urllib.parse
from pathlib import Path
from typing import Any, Dict

from src.providers.base import NormalizedItem, ProviderName

logger = logging.getLogger("PlaybackCapabilityService")

SUPPORTED_LOCAL_FORMATS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".wma"}


class PlaybackCapabilityService:
    """
    Evaluates and enforces truthful, item-specific playback, embed, queue,
    and download capabilities per provider and item.
    Guarantees that actions are never misrepresented as available when unsupported.
    """

    @classmethod
    def resolve_capabilities(cls, item: NormalizedItem) -> Dict[str, Any]:
        prov = item.provider

        if prov == ProviderName.LOCAL.value:
            # Determine path to check
            raw_path = (
                item.capabilities.get("file_path")
                or item.provider_item_id
                or item.canonical_url
                or ""
            )
            file_path = Path(raw_path) if raw_path else None

            # Default / base state for local items
            base_local = {
                "can_access_metadata": True,
                "has_preview": False,
                "can_preview": False,
                "preview_url": None,
                "can_official_playback": False,
                "can_remote_control": False,
                "can_play_local": False,
                "local_file_path": str(file_path) if file_path else None,
                "local_file_status": "none",
                "can_embed": False,
                "embed_url": None,
                "embed_status": "none",
                "can_play": False,
                "playback_type": "none",
                "stream_url": None,
                "can_download": False,
                "download_auth_basis": "already_local",
                "can_queue": False,
                "authorization_status": "local",
                "reason_unavailable": ""
            }

            if not file_path or not file_path.exists() or not file_path.is_file():
                base_local["local_file_status"] = "missing"
                base_local["reason_unavailable"] = "Local audio file not found on disk."
                return base_local

            ext = file_path.suffix.lower()
            if ext not in SUPPORTED_LOCAL_FORMATS:
                base_local["local_file_status"] = "unsupported_format"
                base_local["reason_unavailable"] = f"Unsupported audio format: {ext}"
                return base_local

            try:
                size = file_path.stat().st_size
                if size == 0:
                    base_local["local_file_status"] = "unreadable"
                    base_local["reason_unavailable"] = "Local file is empty (0 bytes)."
                    return base_local
                with open(file_path, "rb") as f:
                    f.read(32)
            except Exception as e:
                base_local["local_file_status"] = "unreadable"
                base_local["reason_unavailable"] = f"Local file is unreadable: {e}"
                return base_local

            # Verified valid local audio file
            base_local.update({
                "has_preview": True,
                "can_preview": True,
                "can_play_local": True,
                "local_file_status": "present",
                "can_play": True,
                "can_queue": True,
                "playback_type": "html5_local",
                "stream_url": f"/api/player/stream?path={urllib.parse.quote(str(file_path))}",
                "reason_unavailable": ""
            })
            return base_local

        elif prov == ProviderName.SPOTIFY.value:
            preview_url = item.capabilities.get("preview_url")
            has_preview = bool(preview_url and str(preview_url).strip())

            # Authorization check for Spotify Connect / Web API remote control
            from src.config_loader import get_credentials_path
            creds_file = get_credentials_path()
            has_auth_session = creds_file.exists() and creds_file.stat().st_size > 0

            can_remote = has_auth_session
            can_official = has_auth_session

            # Playback rollup
            can_play = has_preview or can_remote
            playback_type = "spotify_preview" if has_preview else ("spotify_remote" if can_remote else "none")

            reason = ""
            if not has_preview and not can_remote:
                reason = (
                    "Preview unavailable for this track. Full playback requires Spotify Web/Desktop App remote control "
                    "with active authorization. Use 'Find alternatives' for YouTube Music or YouTube."
                )

            return {
                "can_access_metadata": True,
                "has_preview": has_preview,
                "can_preview": has_preview,
                "preview_url": preview_url if has_preview else None,
                "can_official_playback": can_official,
                "can_remote_control": can_remote,
                "can_play_local": False,
                "local_file_path": None,
                "local_file_status": "none",
                "can_embed": False,
                "embed_url": None,
                "embed_status": "none",
                "can_play": can_play,
                "playback_type": playback_type,
                "stream_url": None,
                "can_download": False,  # Strictly False: protected stream downloading is not supported
                "download_auth_basis": "unsupported",
                "can_queue": True,
                "authorization_status": "authenticated" if has_auth_session else "unauthenticated",
                "reason_unavailable": reason
            }

        elif prov == ProviderName.YTMUSIC.value:
            # Check if embed is disallowed
            is_unplayable = (
                item.source_metadata.get("playable_in_embed") is False
                or item.source_metadata.get("availability") in ("unplayable", "blocked")
                or item.capabilities.get("embed_allowed") is False
            )
            embed_url = f"https://www.youtube.com/embed/{item.provider_item_id}?autoplay=1"

            can_embed = not is_unplayable
            can_play = can_embed
            playback_type = "youtube_embed" if can_play else "none"
            reason = "Embedding disabled by content owner." if is_unplayable else "Media played via authorized YouTube Music embed player."

            return {
                "can_access_metadata": True,
                "has_preview": False,
                "can_preview": False,
                "preview_url": None,
                "can_official_playback": False,
                "can_remote_control": False,
                "can_play_local": False,
                "local_file_path": None,
                "local_file_status": "none",
                "can_embed": can_embed,
                "embed_url": embed_url if can_embed else None,
                "embed_status": "unplayable_embed" if is_unplayable else "allowed",
                "can_play": can_play,
                "playback_type": playback_type,
                "stream_url": None,
                "can_download": False,  # Stream ripping disallowed
                "download_auth_basis": "unsupported",
                "can_queue": True,
                "authorization_status": "public_embed",
                "reason_unavailable": reason
            }

        elif prov == ProviderName.YOUTUBE.value:
            is_unplayable = (
                item.source_metadata.get("playable_in_embed") is False
                or item.source_metadata.get("availability") in ("unplayable", "blocked")
                or item.capabilities.get("embed_allowed") is False
            )
            embed_url = f"https://www.youtube.com/embed/{item.provider_item_id}?autoplay=1"

            can_embed = not is_unplayable
            can_play = can_embed
            playback_type = "youtube_embed" if can_play else "none"
            reason = "Embedding disabled by content owner." if is_unplayable else "Video rendered via official YouTube embedded player."

            return {
                "can_access_metadata": True,
                "has_preview": False,
                "can_preview": False,
                "preview_url": None,
                "can_official_playback": False,
                "can_remote_control": False,
                "can_play_local": False,
                "local_file_path": None,
                "local_file_status": "none",
                "can_embed": can_embed,
                "embed_url": embed_url if can_embed else None,
                "embed_status": "unplayable_embed" if is_unplayable else "allowed",
                "can_play": can_play,
                "playback_type": playback_type,
                "stream_url": None,
                "can_download": False,  # Protected video stream downloading disallowed
                "download_auth_basis": "unsupported",
                "can_queue": True,
                "authorization_status": "public_embed",
                "reason_unavailable": reason
            }

        else:
            return {
                "can_access_metadata": False,
                "has_preview": False,
                "can_preview": False,
                "preview_url": None,
                "can_official_playback": False,
                "can_remote_control": False,
                "can_play_local": False,
                "local_file_path": None,
                "local_file_status": "none",
                "can_embed": False,
                "embed_url": None,
                "embed_status": "none",
                "can_play": False,
                "playback_type": "none",
                "stream_url": None,
                "can_download": False,
                "download_auth_basis": "unsupported",
                "can_queue": False,
                "authorization_status": "unknown",
                "reason_unavailable": f"Unsupported or unknown provider: {prov}"
            }

    @classmethod
    def get_all_provider_statuses(cls) -> Dict[str, Any]:
        """Returns availability and capability summaries for all providers."""
        from src.config_loader import get_config, get_credentials_path
        cfg = get_config()
        sp_cfg = cfg.get("spotify", {})
        has_sp_creds = bool(sp_cfg.get("client_id") and sp_cfg.get("client_secret"))
        creds_file = get_credentials_path()
        has_stored_session = creds_file.exists() and creds_file.stat().st_size > 0

        return {
            "spotify": {
                "display_name": "Spotify",
                "status": "authenticated" if has_stored_session else ("credentials_configured" if has_sp_creds else "zero_key_mode"),
                "is_authoritative": True,
                "can_access_metadata": True,
                "can_preview": True,  # Item-specific: only when item exposes valid preview_url
                "can_remote_playback": has_stored_session,
                "can_download": False,  # Full track downloading is disabled to respect DRM/licensing policies
                "can_search": has_sp_creds,
                "note": "Authoritative primary source for metadata and 30s previews. Full track downloading is disabled to respect DRM/licensing policies."
            },
            "ytmusic": {
                "display_name": "YouTube Music",
                "status": "ready",
                "is_authoritative": False,
                "can_embed": True,
                "can_search": True,
                "can_download": False,
                "note": "Discovery and missing recording alternative. Audio played via official embed player."
            },
            "youtube": {
                "display_name": "YouTube",
                "status": "ready",
                "is_authoritative": False,
                "can_embed": True,
                "can_search": True,
                "can_download": False,
                "note": "Video and live performance discovery. Video played via official IFrame player."
            },
            "local": {
                "display_name": "Local Music Library",
                "status": "ready",
                "is_authoritative": True,
                "can_play": True,
                "can_search": True,
                "can_download": False,
                "playback_type": "html5_local",
                "note": "Locally stored audio tracks on D: drive verified by file existence and format."
            }
        }
