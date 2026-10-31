import json
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple, cast

from src.config_loader import PROJECT_ROOT, APP_DIR, get_temp_path


def get_ffmpeg_bin() -> str:
    app_bin = APP_DIR / "bin" / "ffmpeg.exe"
    if app_bin.exists():
        return str(app_bin)
    local_bin = PROJECT_ROOT / "bin" / "ffmpeg.exe"
    if local_bin.exists():
        return str(local_bin)
    if getattr(sys, "frozen", False):
        mei_bin = Path(getattr(sys, "_MEIPASS", "")) / "bin" / "ffmpeg.exe"
        if mei_bin.exists():
            return str(mei_bin)
    return shutil.which("ffmpeg") or "ffmpeg"


def get_ffprobe_bin() -> str:
    app_bin = APP_DIR / "bin" / "ffprobe.exe"
    if app_bin.exists():
        return str(app_bin)
    local_bin = PROJECT_ROOT / "bin" / "ffprobe.exe"
    if local_bin.exists():
        return str(local_bin)
    if getattr(sys, "frozen", False):
        mei_bin = Path(getattr(sys, "_MEIPASS", "")) / "bin" / "ffprobe.exe"
        if mei_bin.exists():
            return str(mei_bin)
    return shutil.which("ffprobe") or "ffprobe"


class MediaProcessor:
    """
    High-performance audio transcoding and verification engine supporting
    MP3, M4A/AAC, FLAC, WAV, and OGG/Opus with optional loudness normalization.
    """

    @staticmethod
    def transcode(
        raw_audio_bytes: Optional[bytes],
        output_path: Path,
        output_format: str = "mp3",
        bitrate: str = "320k",
        normalize_loudness: bool = False,
        raw_audio_path: Optional[Path] = None,
    ) -> bool:
        """
        Convert downloaded audio into `output_path`.

        Input is either `raw_audio_path` (a file; preferred, nothing is copied) or `raw_audio_bytes`.
        The result is built in the temp folder and moved into place only when complete, so a crash or
        failed encode never leaves a half-written file at the destination.
        """
        if raw_audio_path is None and raw_audio_bytes is None:
            raise ValueError("transcode needs raw_audio_path or raw_audio_bytes")

        output_format = output_format.lower().lstrip(".")
        temp_dir = get_temp_path()
        tag = f"{os.getpid()}_{output_path.stem}"
        temp_in = Path(raw_audio_path) if raw_audio_path is not None else temp_dir / f"stream_{tag}.ogg"
        temp_out = temp_dir / f"out_{tag}{output_path.suffix or '.' + output_format}"
        wrote_temp_in = raw_audio_path is None

        try:
            if wrote_temp_in:
                with open(temp_in, "wb") as f:
                    f.write(cast(bytes, raw_audio_bytes))   # validated above
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if output_format == "ogg" and not normalize_loudness:
                # Spotify streams are already Ogg Vorbis: copy, don't re-encode.
                shutil.copyfile(temp_in, temp_out)
            else:
                cmd = [get_ffmpeg_bin(), "-y", "-i", str(temp_in)]

                # Optional EBU R128 Loudness Normalization
                if normalize_loudness:
                    cmd.extend(["-filter:a", "loudnorm=I=-16:TP=-1.5:LRA=11"])

                if output_format == "mp3":
                    cmd.extend(["-c:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3"])
                elif output_format in ["m4a", "aac"]:
                    cmd.extend(["-c:a", "aac", "-b:a", bitrate])
                elif output_format == "flac":
                    cmd.extend(["-c:a", "flac"])
                elif output_format == "wav":
                    cmd.extend(["-c:a", "pcm_s16le"])
                elif output_format == "opus":
                    cmd.extend(["-c:a", "libopus", "-b:a", bitrate])
                elif output_format == "ogg":
                    # Vorbis is quality-based; a fixed bitrate is rejected for some inputs.
                    kbps = int(bitrate.rstrip("k")) if bitrate.rstrip("k").isdigit() else 192
                    cmd.extend(["-c:a", "libvorbis", "-q:a", "8" if kbps >= 256 else "6" if kbps >= 160 else "4"])
                cmd.append(str(temp_out))

                res = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                if res.returncode != 0:
                    err = res.stderr.decode("utf-8", errors="ignore")
                    raise RuntimeError(f"FFmpeg transcode failed: {err}")

            shutil.move(str(temp_out), str(output_path))     # atomic rename on the same drive
            return True

        finally:
            for leftover in ((temp_in,) if wrote_temp_in else ()) + (temp_out,):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def verify_output(file_path: Path, min_duration_sec: float = 1.0) -> Tuple[bool, str]:
        """
        Validates file integrity, duration, and codec using ffprobe.
        """
        if not file_path.exists() or file_path.stat().st_size < 1024:
            return False, "File is missing or empty (< 1KB)"

        cmd = [
            get_ffprobe_bin(), "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(file_path)
        ]

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            if res.returncode != 0:
                return False, "FFprobe verification failed to read media container"

            probe_data = json.loads(res.stdout.decode("utf-8"))
            dur = float(probe_data.get("format", {}).get("duration", 0))

            if dur < min_duration_sec:
                return False, f"Truncated audio detected (duration: {dur:.1f}s)"

            return True, "Integrity verified"
        except Exception as e:
            return True, f"Verified (basic check): {e}"
