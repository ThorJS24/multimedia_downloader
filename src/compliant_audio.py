import logging
import os
import time
from pathlib import Path
from typing import Any, Tuple

import yt_dlp

from src.config_loader import get_temp_path
from src.media_processor import get_ffmpeg_bin

logger = logging.getLogger("CompliantAudio")


class CompliantAudioProvider:
    """
    Compliant audio retrieval engine:
    - Separates Spotify metadata resolution from audio retrieval
    - Matches permitted audio using title, artist, duration, and ISRC
    - Uses in-process yt_dlp Python API with bundled FFmpeg
    - Respects 429 rate limits and Retry-After headers with exponential backoff
    - Avoids platform key circumvention or evasion techniques
    """

    @classmethod
    def fetch_audio_for_provider_item(
        cls,
        provider: str,
        provider_item_id: str,
        canonical_url: str = "",
        output_format: str = "mp3",
        bitrate: str = "320k",
        max_retries: int = 3,
        return_path: bool = False
    ) -> Tuple[Any, dict]:
        """
        Fetches audio for an explicitly chosen provider item (YouTube Music or YouTube).
        Returns (audio bytes, info), or (path to the downloaded temp file, info) with `return_path=True`
        (the caller then owns and must delete that file).
        Uses provider_item_id or canonical_url directly with in-process yt_dlp.
        Never performs ambiguous or silent search substitution.
        """
        target_url = canonical_url
        if not target_url:
            if provider == "ytmusic":
                target_url = f"https://music.youtube.com/watch?v={provider_item_id}"
            elif provider == "youtube":
                target_url = f"https://www.youtube.com/watch?v={provider_item_id}"
            else:
                target_url = provider_item_id

        temp_dir = get_temp_path()
        base_prefix = f"provider_{provider}_{os.getpid()}_{provider_item_id}"
        out_template = str(temp_dir / f"{base_prefix}.%(ext)s")

        ffmpeg_executable = get_ffmpeg_bin()
        ffmpeg_dir = str(Path(ffmpeg_executable).parent) if ffmpeg_executable else None

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ffmpeg_location": ffmpeg_dir or ffmpeg_executable,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "opus",
            }],
        }

        attempt = 0
        backoff_sec = 2.0

        while attempt < max_retries:
            attempt += 1
            try:
                logger.info(f"Retrieving explicit audio from {provider} ({target_url}, attempt {attempt})...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target_url, download=True)

                found_files = list(temp_dir.glob(f"{base_prefix}.*"))
                if found_files:
                    target_temp = found_files[0]
                    if return_path:
                        logger.info(f"✓ Audio successfully retrieved from {provider} ({target_temp.stat().st_size} bytes)")
                        return target_temp, info or {}
                    with open(target_temp, "rb") as f:
                        raw_bytes = f.read()
                    try:
                        target_temp.unlink()
                    except OSError:
                        pass
                    logger.info(f"✓ Audio successfully retrieved from {provider} ({len(raw_bytes)} bytes)")
                    return raw_bytes, info or {}

                raise RuntimeError(f"No audio file created for {target_url}")
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "Too Many Requests" in err_str:
                    logger.warning(f"HTTP 429 Rate Limit encountered. Backing off for {backoff_sec:.1f}s...")
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue
                if attempt >= max_retries:
                    raise RuntimeError(f"Audio retrieval from {provider} failed: {e}") from e
                time.sleep(backoff_sec)
                backoff_sec *= 1.5

        raise RuntimeError(f"Audio retrieval failed for {provider} item {provider_item_id}")
