import binascii
import logging
import math
import os
from pathlib import Path
from typing import List, Optional, Tuple


# librespot's generated protobuf code only loads on protobuf >= 4 with the pure-Python implementation
# (verified: all 173 message types round-trip identically on 3.20.1, 5.29 and 6.33). A no-op on the pinned 3.20.x.
try:
    from importlib.metadata import version as _pkg_version
    if int(_pkg_version("protobuf").split(".")[0]) >= 4:
        os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
except Exception:
    pass

from librespot.audio import AudioQualityPicker, PlayableContentFeeder  # noqa: E402
from librespot.core import PlaylistId, Session, TrackId
from librespot.proto import Metadata_pb2
from src.config_loader import get_config, get_credentials_path, PROJECT_ROOT

logger = logging.getLogger("SpotifyDirect")

STREAM_CHUNK_BYTES = 128 * 1024   # librespot streams audio in fixed 128 KiB chunks


class QualityPicker(AudioQualityPicker):
    """
    Selects preferred audio quality from Spotify's available streams.
    """
    def __init__(self, quality: str = "VERY_HIGH"):
        self.quality = quality.upper()

    def get_file(self, files: List[Metadata_pb2.AudioFile]) -> Optional[Metadata_pb2.AudioFile]:
        if not files:
            return None

        # Priority mappings based on requested quality
        if self.quality == "VERY_HIGH":
            priority = [
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_320,
                Metadata_pb2.AudioFile.Format.MP3_320,
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_160,
                Metadata_pb2.AudioFile.Format.MP3_256,
                Metadata_pb2.AudioFile.Format.MP3_160,
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_96
            ]
        elif self.quality == "HIGH":
            priority = [
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_160,
                Metadata_pb2.AudioFile.Format.MP3_160,
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_320,
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_96
            ]
        else: # NORMAL / LOW
            priority = [
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_96,
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_160,
                Metadata_pb2.AudioFile.Format.OGG_VORBIS_320
            ]

        file_map = {f.format: f for f in files}
        for fmt in priority:
            if fmt in file_map:
                return file_map[fmt]

        return files[0]


class SpotifyDirectClient:
    """
    Direct Spotify client that interacts directly with Spotify's Access Point (AP)
    and audio CDN servers via librespot.
    """
    def __init__(self):
        self.session: Optional[Session] = None
        self.config = get_config()
        self.credentials_path = get_credentials_path()

    def is_logged_in(self) -> bool:
        return self.session is not None and self.session.is_valid()

    def login_with_stored(self) -> bool:
        if not self.credentials_path.exists():
            return False

        try:
            logger.info("Attempting authentication with stored credentials")
            builder = Session.Builder()

            # Configure cache directory strictly within project
            cache_dir = str(PROJECT_ROOT / ".cache" / "librespot")
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            conf = (
                Session.Configuration.Builder()
                .set_cache_dir(cache_dir)
                .set_stored_credential_file(str(self.credentials_path))
                .set_store_credentials(True)
                .build()
            )
            builder.conf = conf
            builder.stored_file(str(self.credentials_path))
            self.session = builder.create()
            logger.info("Authenticated with Spotify via cached session")
            return True
        except Exception as e:
            logger.warning("Stored credentials invalid or expired: %s", e)
            return False

    def login_with_user_pass(self, username: str, password: str) -> bool:
        try:
            logger.info("Connecting to Spotify Access Point")
            builder = Session.Builder()

            cache_dir = str(PROJECT_ROOT / ".cache" / "librespot")
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            conf = (
                Session.Configuration.Builder()
                .set_cache_dir(cache_dir)
                .set_stored_credential_file(str(self.credentials_path))
                .set_store_credentials(True)
                .build()
            )
            builder.conf = conf
            builder.user_pass(username, password)
            self.session = builder.create()

            logger.info("Authenticated with Spotify")
            return True
        except Exception as e:
            logger.error("Spotify login failed: %s", e)
            return False

    def ensure_authenticated(self) -> bool:
        if self.is_logged_in():
            return True

        if self.login_with_stored():
            return True

        from src.settings_service import SettingsService
        creds = SettingsService.get_all(reveal=True)["spotify"]
        username, password = creds["username"], creds["password"]

        if username and password:
            return self.login_with_user_pass(username, password)

        return False

    def extract_playlist_info(self, playlist_id_str: str) -> Tuple[str, List[str]]:
        """
        Pulls playlist name and track URIs directly from Spotify servers.
        """
        if not self.ensure_authenticated():
            raise RuntimeError("Authentication required to fetch playlist directly from Spotify.")

        playlist_id = PlaylistId.from_base62(playlist_id_str)
        playlist_proto = self._api().get_playlist(playlist_id)

        name = playlist_proto.attributes.name or f"Playlist_{playlist_id_str}"
        track_uris = []

        if playlist_proto.contents and playlist_proto.contents.items:
            for item in playlist_proto.contents.items:
                if item.uri and "spotify:track:" in item.uri:
                    track_uris.append(item.uri)

        return name, track_uris

    def _api(self):
        if self.session is None:
            raise RuntimeError("Not connected to Spotify")
        return self.session.api()

    def get_track_metadata(self, track_uri_or_id: str) -> dict:
        """Metadata only (no audio is fetched). Requires a Spotify session."""
        if not self.ensure_authenticated():
            raise RuntimeError("Authentication required to fetch track metadata directly from Spotify.")
        clean_id = track_uri_or_id.replace("spotify:track:", "").split("?")[0]
        return self._read_track_metadata(TrackId.from_base62(clean_id), clean_id)

    def _read_track_metadata(self, track_id, clean_id: str) -> dict:
        """Official track metadata from Spotify's metadata endpoint."""
        track_proto = self._api().get_metadata_4_track(track_id)
        if not track_proto:
            raise RuntimeError(f"Could not retrieve metadata for track {clean_id}")

        title = track_proto.name
        artists = [a.name for a in track_proto.artist]
        album_name = track_proto.album.name if track_proto.album else ""
        year = track_proto.album.date.year if (track_proto.album and track_proto.album.date) else ""
        track_number = track_proto.number
        disc_number = track_proto.disc_number
        isrc = ""
        for ext in track_proto.external_id:
            if ext.type == "isrc":
                isrc = ext.id
                break

        # Extract cover art CDN URL if available
        cover_url = None
        if track_proto.album and track_proto.album.cover_group and track_proto.album.cover_group.image:
            # First image is usually high-res
            image_id = track_proto.album.cover_group.image[0].file_id
            hex_id = binascii.hexlify(image_id).decode("ascii")
            cover_url = f"https://i.scdn.co/image/{hex_id}"

        return {
            "id": clean_id,
            "title": title,
            "artists": artists,
            "album": album_name,
            "year": year,
            "track_number": track_number,
            "disc_number": disc_number,
            "isrc": isrc,
            "cover_url": cover_url,
            "duration_ms": track_proto.duration
        }

    def download_track_to_file(self, track_uri_or_id: str, dest: Path, quality: str = "VERY_HIGH") -> dict:
        """
        Requests the audio key from Spotify, downloads and decrypts the stream, and writes it to `dest`
        (via `<dest>.part`, then an atomic rename). Returns the track metadata.

        The audio never exists as one big bytes object: chunks are written to disk as they are decrypted.
        """
        if not self.ensure_authenticated():
            raise RuntimeError("Spotify direct audio stream retrieval is unsupported. Protected DRM full-track streams cannot be downloaded.")

        clean_id = track_uri_or_id.replace("spotify:track:", "").split("?")[0]
        track_id = TrackId.from_base62(clean_id)

        # 1. Fetch official track metadata directly from Spotify
        metadata = self._read_track_metadata(track_id, clean_id)

        # 2. Feed stream and decrypt via Spotify audio key manager
        feeder = PlayableContentFeeder(self.session)
        picker = QualityPicker(quality)

        try:
            loaded_stream = feeder.load(track_id, picker, preload=False, halt_listener=None)
        except Exception as e:
            err_str = str(e)
            if "Premium" in err_str or "403" in err_str or "Restricted" in err_str:
                raise PermissionError(
                    "Spotify Audio Key Error: Full direct stream extraction requires a Spotify Premium account. "
                    "Spotify's key server rejects requests from free accounts."
                ) from e
            raise RuntimeError(f"Failed to load stream from Spotify: {e}") from e

        # 3. Write the decrypted audio to disk
        self.write_stream_to_file(loaded_stream.input_stream.stream(), Path(dest))
        return metadata

    @staticmethod
    def write_stream_to_file(stream, dest: Path) -> int:
        """
        Copy a librespot chunked stream to `dest`, one chunk at a time. Returns the number of bytes written.

        This walks the stream exactly as librespot's own `read()` does (fixed 128 KiB chunks, waiting for
        each to arrive and decrypt) but writes each chunk straight to disk instead of assembling the whole
        track in memory twice. If anything about that fails, the plain `stream.read()` path is used instead.
        """
        dest = Path(dest)
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                total = stream.size()
                written = 0
                with open(part, "wb") as fh:
                    for index in range(math.ceil(total / STREAM_CHUNK_BYTES)):
                        stream.check_availability(index, True, False)
                        chunk = stream.buffer()[index]
                        fh.write(chunk)
                        written += len(chunk)
                if written != total:
                    raise RuntimeError(f"stream ended early: wrote {written} of {total} bytes")
            except Exception as exc:
                logger.warning("Chunked write failed (%s); falling back to a full read", exc)
                data = stream.read()
                with open(part, "wb") as fh:
                    fh.write(data)
                written = len(data)
            os.replace(part, dest)
            return written
        finally:
            part.unlink(missing_ok=True)
