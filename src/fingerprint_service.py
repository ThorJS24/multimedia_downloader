import cmath
import hashlib
import json
import logging
import math
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.db import get_db_connection
from src.media_processor import get_ffmpeg_bin

logger = logging.getLogger("FingerprintService")

SAMPLE_RATE = 11025
FRAME_SIZE = 2048                       # ~186 ms per frame, ~5.4 Hz per FFT bin
HOP_SIZE = 2048                         # non-overlapping frames keep pure-Python FFT time to ~1 s/min
SILENCE_RMS = 60.0                      # int16 RMS below this counts as silence
# Band edges in Hz: sub-bass/bass, low-mid, mid, upper-mid, presence, treble
BAND_EDGES_HZ = (60, 250, 500, 1000, 2000, 4000, 5000)
ALGO_VERSION = 2

_HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / (FRAME_SIZE - 1)) for i in range(FRAME_SIZE)]
_TWIDDLE = [cmath.exp(-2j * math.pi * k / FRAME_SIZE) for k in range(FRAME_SIZE // 2)]
_BITREV = []
for _i in range(FRAME_SIZE):
    _r, _x = 0, _i
    for _ in range(FRAME_SIZE.bit_length() - 1):
        _r = (_r << 1) | (_x & 1)
        _x >>= 1
    _BITREV.append(_r)
_BAND_BINS = [
    (max(1, int(lo * FRAME_SIZE / SAMPLE_RATE)), int(hi * FRAME_SIZE / SAMPLE_RATE))
    for lo, hi in zip(BAND_EDGES_HZ[:-1], BAND_EDGES_HZ[1:], strict=True)
]


def _band_energies(frame) -> List[float]:
    """Windowed radix-2 FFT of one frame, summed into BAND_EDGES_HZ bands."""
    x = [frame[_BITREV[i]] * _HANN[_BITREV[i]] + 0j for i in range(FRAME_SIZE)]
    size = 2
    while size <= FRAME_SIZE:
        half, step = size // 2, FRAME_SIZE // size
        for start in range(0, FRAME_SIZE, size):
            for k in range(half):
                i, j = start + k, start + k + half
                t = x[j] * _TWIDDLE[k * step]
                x[j] = x[i] - t
                x[i] = x[i] + t
        size *= 2
    return [sum(abs(x[b]) ** 2 for b in range(lo, hi)) for lo, hi in _BAND_BINS]


class FingerprintService:
    """
    Portable spectral fingerprinting using the bundled FFmpeg for decoding and a pure-Python FFT.
    Each ~186 ms frame is reduced to its dominant frequency band (or 'S' for silence); the token
    sequence is the fingerprint. Similar recordings produce similar sequences even when re-encoded,
    while different material (or a different pitch range) does not.
    """

    @staticmethod
    def extract_pcm_samples(file_path: Path, max_duration_sec: float = 60.0) -> Tuple[bytes, float]:
        """Decodes to 11025 Hz 16-bit mono PCM with the bundled FFmpeg."""
        cmd = [
            get_ffmpeg_bin(),
            "-v", "quiet",
            "-i", str(file_path),
            "-t", str(max_duration_sec),
            "-f", "s16le",
            "-ac", "1",
            "-ar", str(SAMPLE_RATE),
            "-"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg failed to extract PCM: {res.stderr.decode('utf-8', errors='ignore')}")

        pcm_data = res.stdout
        duration = len(pcm_data) / (SAMPLE_RATE * 2)
        return pcm_data, duration

    @classmethod
    def compute_fingerprint(cls, file_path: Path, max_duration_sec: float = 60.0) -> Dict[str, Any]:
        """Computes (or loads from cache) the spectral fingerprint of a file."""
        conn = get_db_connection()
        str_path = str(file_path)

        cached = conn.execute("SELECT * FROM fingerprints WHERE path = ?", (str_path,)).fetchone()
        if cached and cached["tokens"]:
            return {
                "path": str_path,
                "hash": cached["hash"],
                "duration_sec": cached["duration_sec"],
                "tokens": cached["tokens"],
                "peaks": json.loads(cached["pcm_peaks"]) if cached["pcm_peaks"] else [],
                "cached": True
            }

        pcm_bytes, dur = cls.extract_pcm_samples(file_path, max_duration_sec)
        if len(pcm_bytes) < FRAME_SIZE * 2:
            return {"path": str_path, "hash": "", "duration_sec": 0.0, "tokens": "", "peaks": [],
                    "error": "Insufficient audio"}

        num_samples = len(pcm_bytes) // 2
        samples = struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples * 2])

        tokens: List[str] = []
        peaks: List[Dict[str, Any]] = []
        for start in range(0, num_samples - FRAME_SIZE + 1, HOP_SIZE):
            frame = samples[start:start + FRAME_SIZE]
            rms = math.sqrt(sum(s * s for s in frame) / FRAME_SIZE)
            if rms < SILENCE_RMS:
                tokens.append("S")
                continue
            energies = _band_energies(frame)
            band = energies.index(max(energies))
            tokens.append(str(band))
            if len(peaks) < 128:
                peaks.append({"frame": start // HOP_SIZE, "dominant_band": band, "energy": round(max(energies))})

        token_str = "".join(tokens)
        acoustic_hash = hashlib.sha256(token_str.encode("utf-8")).hexdigest()
        stored_peaks = peaks[:64]

        with conn:
            conn.execute(
                "INSERT INTO fingerprints (path, hash, duration_sec, pcm_peaks, tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, "
                "duration_sec=excluded.duration_sec, pcm_peaks=excluded.pcm_peaks, tokens=excluded.tokens, "
                "created_at=excluded.created_at",
                (str_path, acoustic_hash, round(dur, 2), json.dumps(stored_peaks), token_str, time.time())
            )

        return {
            "path": str_path,
            "hash": acoustic_hash,
            "duration_sec": round(dur, 2),
            "tokens": token_str,
            "peaks": stored_peaks,
            "cached": False
        }

    @classmethod
    def similarity(cls, tokens_a: str, tokens_b: str) -> float:
        """Share of aligned, non-silent frames whose dominant band matches (0.0 - 1.0)."""
        if not tokens_a or not tokens_b:
            return 0.0
        if tokens_a == tokens_b:
            return 1.0
        pairs = [(a, b) for a, b in zip(tokens_a, tokens_b, strict=False) if not (a == "S" and b == "S")]
        if not pairs:
            return 0.0
        matches = sum(1 for a, b in pairs if a == b)
        return round(matches / max(len(tokens_a), len(tokens_b)), 2)

    @classmethod
    def compare_files(cls, path_a: Path, path_b: Path) -> float:
        """Acoustic similarity (0.0 to 1.0) between two files."""
        fp_a = cls.compute_fingerprint(path_a)
        fp_b = cls.compute_fingerprint(path_b)
        if not fp_a.get("hash") or not fp_b.get("hash"):
            return 0.0
        if fp_a["hash"] == fp_b["hash"]:
            return 1.0
        return cls.similarity(fp_a.get("tokens", ""), fp_b.get("tokens", ""))

    @classmethod
    def find_all_acoustic_duplicates(cls, threshold: float = 0.85) -> List[Dict[str, Any]]:
        """Scans all indexed library tracks and flags acoustic duplicates."""
        conn = get_db_connection()
        files = conn.execute("SELECT path, title, artist, album FROM library_files").fetchall()

        fingerprints = []
        for f in files:
            p = Path(f["path"])
            if not p.exists():
                continue
            try:
                fp = cls.compute_fingerprint(p)
            except Exception as e:
                logger.warning(f"Error fingerprinting {p.name}: {e}")
                continue
            if fp.get("hash"):
                fingerprints.append({"info": dict(f), "fp": fp})

        duplicates = []
        for i in range(len(fingerprints)):
            for j in range(i + 1, len(fingerprints)):
                a, b = fingerprints[i], fingerprints[j]
                if a["fp"]["hash"] == b["fp"]["hash"]:
                    duplicates.append({"file_a": a["info"], "file_b": b["info"],
                                       "similarity": 1.0, "type": "Exact Acoustic Match"})
                    continue
                sim = cls.similarity(a["fp"].get("tokens", ""), b["fp"].get("tokens", ""))
                if sim >= threshold:
                    duplicates.append({"file_a": a["info"], "file_b": b["info"],
                                       "similarity": sim, "type": "High Acoustic Similarity"})
        return duplicates
