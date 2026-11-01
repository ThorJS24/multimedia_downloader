import base64
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from mutagen.id3 import (
    ID3, TIT2, TPE1, TPE2, TALB, TDRC, TRCK, TPOS, TSRC, TCON, USLT, COMM, APIC, ID3NoHeaderError
)
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

from src.config_loader import get_temp_path

logger = logging.getLogger("TagWriter")


class TagWriter:
    """
    Exhaustive metadata and artwork embedder supporting ID3v2, Vorbis Comments,
    FLAC Pictures, and MP4/M4A tags with lyrics support.
    Protects original files with atomic backup buffers during tagging.
    """

    @classmethod
    def apply_tags(
        cls,
        file_path: Path,
        meta: dict,
        cover_image_bytes: Optional[bytes] = None,
        save_cover_art_file: bool = False
    ) -> bool:
        ext = file_path.suffix.lower()

        # Save separate cover.jpg in album/track directory if enabled
        if save_cover_art_file and cover_image_bytes:
            cover_dest = file_path.parent / "cover.jpg"
            if not cover_dest.exists():
                try:
                    with open(cover_dest, "wb") as f:
                        f.write(cover_image_bytes)
                except Exception:
                    logger.warning("Could not save %s (cover art file next to the track)", cover_dest, exc_info=True)

        # Protect against partial file corruption with temporary buffer
        temp_dir = get_temp_path()
        backup_path = temp_dir / f"tag_bak_{os.getpid()}_{file_path.name}"
        shutil.copy2(file_path, backup_path)

        try:
            success = False
            if ext == ".mp3":
                success = cls._tag_mp3(file_path, meta, cover_image_bytes)
            elif ext == ".flac":
                success = cls._tag_flac(file_path, meta, cover_image_bytes)
            elif ext == ".ogg":
                success = cls._tag_ogg(file_path, meta, cover_image_bytes)
            elif ext in [".m4a", ".mp4", ".aac"]:
                success = cls._tag_mp4(file_path, meta, cover_image_bytes)
            elif ext == ".wav":
                success = cls._tag_wav(file_path, meta, cover_image_bytes)

            if success and backup_path.exists():
                try:
                    backup_path.unlink()
                except OSError:
                    pass
            return success
        except Exception as e:
            # Revert from temporary backup to prevent corrupt audio
            if backup_path.exists():
                try:
                    shutil.copy2(backup_path, file_path)
                    backup_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Tag writing failed, original file preserved: {e}") from e

    @classmethod
    def _populate_id3(cls, tags: ID3, meta: dict, cover_bytes: Optional[bytes]) -> None:
        if meta.get("title"):
            tags.add(TIT2(encoding=3, text=meta["title"]))

        artists_val = meta.get("artists", [])
        artists_str = ", ".join(artists_val) if isinstance(artists_val, list) else str(artists_val)
        if artists_str:
            tags.add(TPE1(encoding=3, text=artists_str))

        if meta.get("album_artist"):
            tags.add(TPE2(encoding=3, text=meta["album_artist"]))

        if meta.get("album"):
            tags.add(TALB(encoding=3, text=meta["album"]))

        if meta.get("year"):
            tags.add(TDRC(encoding=3, text=str(meta["year"])))

        if meta.get("track_number"):
            tot = meta.get("total_tracks", meta["track_number"])
            tags.add(TRCK(encoding=3, text=f"{meta['track_number']}/{tot}"))

        if meta.get("disc_number"):
            tot_d = meta.get("total_discs", 1)
            tags.add(TPOS(encoding=3, text=f"{meta['disc_number']}/{tot_d}"))

        if meta.get("isrc"):
            tags.add(TSRC(encoding=3, text=meta["isrc"]))

        if meta.get("genre"):
            tags.add(TCON(encoding=3, text=meta["genre"]))

        if meta.get("lyrics"):
            tags.add(USLT(encoding=3, lang="eng", desc="Lyrics", text=meta["lyrics"]))

        if meta.get("comment"):
            tags.add(COMM(encoding=3, lang="eng", desc="Comment", text=meta["comment"]))

        if cover_bytes:
            tags.add(APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,  # Cover (front)
                desc="Cover",
                data=cover_bytes
            ))

    @classmethod
    def _tag_mp3(cls, file_path: Path, meta: dict, cover_bytes: Optional[bytes]) -> bool:
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()

        cls._populate_id3(tags, meta, cover_bytes)
        tags.save(file_path, v2_version=3)
        return True

    @classmethod
    def _tag_wav(cls, file_path: Path, meta: dict, cover_bytes: Optional[bytes]) -> bool:
        audio = WAVE(file_path)
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None   # add_tags() above guarantees it
        cls._populate_id3(audio.tags, meta, cover_bytes)
        audio.save()
        return True

    @staticmethod
    def _tag_flac(file_path: Path, meta: dict, cover_bytes: Optional[bytes]) -> bool:
        audio = FLAC(file_path)
        if meta.get("title"): audio["TITLE"] = meta["title"]
        artists_val = meta.get("artists", [])
        if artists_val:
            audio["ARTIST"] = ", ".join(artists_val) if isinstance(artists_val, list) else str(artists_val)
        if meta.get("album"): audio["ALBUM"] = meta["album"]
        if meta.get("album_artist"): audio["ALBUMARTIST"] = meta["album_artist"]
        if meta.get("year"): audio["DATE"] = str(meta["year"])
        if meta.get("track_number"): audio["TRACKNUMBER"] = str(meta["track_number"])
        if meta.get("disc_number"): audio["DISCNUMBER"] = str(meta["disc_number"])
        if meta.get("isrc"): audio["ISRC"] = meta["isrc"]
        if meta.get("genre"): audio["GENRE"] = meta["genre"]
        if meta.get("lyrics"): audio["LYRICS"] = meta["lyrics"]

        if cover_bytes:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Front Cover"
            pic.data = cover_bytes
            audio.clear_pictures()
            audio.add_picture(pic)

        audio.save()
        return True

    @staticmethod
    def _tag_ogg(file_path: Path, meta: dict, cover_bytes: Optional[bytes]) -> bool:
        audio = OggVorbis(file_path)
        if meta.get("title"): audio["title"] = meta["title"]
        artists_val = meta.get("artists", [])
        if artists_val:
            audio["artist"] = ", ".join(artists_val) if isinstance(artists_val, list) else str(artists_val)
        if meta.get("album"): audio["album"] = meta["album"]
        if meta.get("album_artist"): audio["albumartist"] = meta["album_artist"]
        if meta.get("year"): audio["date"] = str(meta["year"])
        if meta.get("track_number"): audio["tracknumber"] = str(meta["track_number"])
        if meta.get("disc_number"): audio["discnumber"] = str(meta["disc_number"])
        if meta.get("isrc"): audio["isrc"] = meta["isrc"]
        if meta.get("genre"): audio["genre"] = meta["genre"]
        if meta.get("lyrics"): audio["lyrics"] = meta["lyrics"]

        if cover_bytes:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Front Cover"
            pic.data = cover_bytes
            data = base64.b64encode(pic.write()).decode("ascii")
            audio["metadata_block_picture"] = [data]

        audio.save()
        return True

    @staticmethod
    def _tag_mp4(file_path: Path, meta: dict, cover_bytes: Optional[bytes]) -> bool:
        mp4 = MP4(file_path)
        if meta.get("title"): mp4["\xa9nam"] = [meta["title"]]
        artists_val = meta.get("artists", [])
        if artists_val:
            mp4["\xa9ART"] = [", ".join(artists_val) if isinstance(artists_val, list) else str(artists_val)]
        if meta.get("album"): mp4["\xa9alb"] = [meta["album"]]
        if meta.get("album_artist"): mp4["aART"] = [meta["album_artist"]]
        if meta.get("year"): mp4["\xa9day"] = [str(meta["year"])]
        if meta.get("track_number"):
            mp4["trkn"] = [(int(meta["track_number"]), int(meta.get("total_tracks", 1)))]
        if meta.get("disc_number"):
            mp4["disk"] = [(int(meta["disc_number"]), int(meta.get("total_discs", 1)))]
        if meta.get("genre"): mp4["\xa9gen"] = [meta["genre"]]
        if meta.get("lyrics"): mp4["\xa9lyr"] = [meta["lyrics"]]

        if cover_bytes:
            mp4["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

        mp4.save()
        return True
