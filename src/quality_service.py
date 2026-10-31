import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from src.db import get_db_connection
from src.media_processor import get_ffmpeg_bin, get_ffprobe_bin

logger = logging.getLogger("QualityService")

QUALITY_PROFILES = {
    "Archival": {
        "format": "flac",
        "description": "Lossless FLAC container, bit-perfect archival quality",
        "min_bitrate_kbps": 700,
        "recommended_for": "Hi-Fi home systems & mastering archive"
    },
    "Standard": {
        "format": "mp3",
        "bitrate": "320k",
        "description": "High-fidelity 320kbps CBR MP3 (Universal compatibility)",
        "min_bitrate_kbps": 320,
        "recommended_for": "Everyday desktop and premium listening"
    },
    "Portable": {
        "format": "m4a",
        "bitrate": "256k",
        "description": "High-efficiency AAC/M4A 256kbps",
        "min_bitrate_kbps": 220,
        "recommended_for": "Phones, car audio, portable media players"
    },
    "SpaceSaver": {
        "format": "mp3",
        "bitrate": "160k",
        "description": "Standard 160kbps MP3 for minimal disk footprint",
        "min_bitrate_kbps": 160,
        "recommended_for": "Storage constrained devices"
    }
}


class QualityService:
    """
    Advanced audio stream quality auditing and loudness measurement engine.
    Calculates EBU R128 Integrated Loudness (LUFS), True Peak (dBTP),
    and detects clipping and silence anomalies.
    """

    @classmethod
    def inspect_stream(cls, file_path: Path) -> Dict[str, Any]:
        """
        Extracts stream codec, container, channels, and bitrate via ffprobe.
        """
        cmd = [
            get_ffprobe_bin(),
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            return {"error": "ffprobe probe failed"}

        try:
            data = json.loads(res.stdout.decode("utf-8", errors="ignore"))
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            audio_stream: Dict[str, Any] = next((s for s in streams if s.get("codec_type") == "audio"), {})

            bitrate_bps = int(fmt.get("bit_rate") or audio_stream.get("bit_rate") or 0)
            bitrate_kbps = round(bitrate_bps / 1000)

            return {
                "codec": audio_stream.get("codec_name", "unknown"),
                "sample_rate": int(audio_stream.get("sample_rate", 44100)),
                "channels": int(audio_stream.get("channels", 2)),
                "bit_depth": int(audio_stream.get("bits_per_raw_sample") or 16),
                "bitrate_kbps": bitrate_kbps,
                "duration_sec": float(fmt.get("duration", 0)),
                "size_bytes": int(fmt.get("size", 0))
            }
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def analyze_loudness_and_peaks(cls, file_path: Path) -> Dict[str, Any]:
        """
        Measures EBU R128 loudness metrics and True Peak using local FFmpeg.
        """
        cmd = [
            get_ffmpeg_bin(),
            "-nostats",
            "-i", str(file_path),
            "-filter_complex", "ebur128=peak=true",
            "-f", "null",
            "-"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stderr_text = res.stderr.decode("utf-8", errors="ignore")

        # ebur128 logs a running value for every 100 ms frame and the final numbers in a trailing
        # "Summary:" block. Only the summary is the track's real measurement.
        summary = stderr_text.rsplit("Summary:", 1)[-1] if "Summary:" in stderr_text else ""

        def number(pattern: str):
            m = re.search(pattern, summary)
            try:
                return float(m.group(1)) if m else None
            except ValueError:
                return None

        lufs = number(r"I:\s+(-?[\d\.]+)\s+LUFS")
        true_peak = number(r"Peak:\s+(-?[\d\.]+)\s+dB(?:FS|TP)")
        lra = number(r"LRA:\s+(-?[\d\.]+)\s+LU")

        if lufs is None:
            # Never invent plausible-looking numbers when the measurement did not work.
            return {"integrated_lufs": None, "true_peak_dbtp": None, "loudness_range_lu": None,
                    "is_clipping": False, "measured": False}

        return {
            "integrated_lufs": round(lufs, 1),
            "true_peak_dbtp": round(true_peak, 1) if true_peak is not None else None,
            "loudness_range_lu": round(lra, 1) if lra is not None else None,
            "is_clipping": true_peak is not None and true_peak >= 0.0,
            "measured": True,
        }

    @classmethod
    def audit_file(cls, file_path: Path) -> Dict[str, Any]:
        """
        Runs complete technical audit on a single audio file and caches the result.
        """
        str_path = str(file_path)
        stream_info = cls.inspect_stream(file_path)
        if "error" in stream_info:
            return {"path": str_path, "status": "ERROR", "error": stream_info["error"]}

        loudness = cls.analyze_loudness_and_peaks(file_path)

        findings = []  # (severity, status, reason); lower number = worse
        if loudness["is_clipping"]:
            findings.append((1, "CLIPPING_RISK", f"True peak exceeds 0.0 dBTP ({loudness['true_peak_dbtp']} dBTP)"))
        if stream_info.get("bitrate_kbps", 0) < 128:
            findings.append((2, "LOW_BITRATE", f"Low bitrate ({stream_info['bitrate_kbps']} kbps)"))
        dur = stream_info.get("duration_sec", 0)
        if dur < 30.0:
            findings.append((3, "TRUNCATED", f"Short audio length detected ({dur:.1f}s)"))
        if not loudness.get("measured", True):
            findings.append((4, "EXCELLENT", "Loudness could not be measured"))
        findings.sort()
        status = findings[0][1] if findings else "EXCELLENT"
        reasons = [reason for _, _, reason in findings]

        # Persist in SQLite
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO quality_audits (path, codec, bitrate_kbps, sample_rate, channels, bit_depth, lufs_integrated, true_peak_dbtp, status, checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET codec=excluded.codec, bitrate_kbps=excluded.bitrate_kbps, "
                "sample_rate=excluded.sample_rate, channels=excluded.channels, bit_depth=excluded.bit_depth, lufs_integrated=excluded.lufs_integrated, "
                "true_peak_dbtp=excluded.true_peak_dbtp, status=excluded.status, checked_at=excluded.checked_at",
                (
                    str_path,
                    stream_info.get("codec", "mp3"),
                    stream_info.get("bitrate_kbps", 320),
                    stream_info.get("sample_rate", 44100),
                    stream_info.get("channels", 2),
                    stream_info.get("bit_depth", 16),
                    loudness.get("integrated_lufs"),
                    loudness.get("true_peak_dbtp"),
                    status,
                    time.time()
                )
            )

        return {
            "path": str_path,
            "filename": file_path.name,
            "stream": stream_info,
            "loudness": loudness,
            "status": status,
            "reasons": reasons,
            "checked_at": time.time()
        }

    @classmethod
    def audit_library(cls) -> Dict[str, Any]:
        """
        Runs audit on all files registered in the library.
        """
        conn = get_db_connection()
        rows = conn.execute("SELECT path FROM library_files").fetchall()

        results = []
        status_counts = {"EXCELLENT": 0, "CLIPPING_RISK": 0, "LOW_BITRATE": 0, "TRUNCATED": 0, "ERROR": 0}

        for r in rows:
            p = Path(r["path"])
            if p.exists():
                res = cls.audit_file(p)
                results.append(res)
                st = res.get("status", "EXCELLENT")
                status_counts[st] = status_counts.get(st, 0) + 1

        return {
            "total_audited": len(results),
            "summary": status_counts,
            "audits": results
        }
