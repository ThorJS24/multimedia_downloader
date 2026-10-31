import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import mutagen

from src.db import get_db_connection, record_audit
from src.metadata_service import MetadataService
from src.tag_writer import TagWriter

logger = logging.getLogger("ReleaseComparison")


class ReleaseComparisonStudio:
    """
    Side-by-side release comparator, field-level metadata provenance engine,
    and interactive tag diff / patch resolver.
    """

    @classmethod
    def compare_releases(cls, id_a: str, id_b: str, kind: str = "album") -> Dict[str, Any]:
        """
        Compares two Spotify releases side by side.
        """
        ms = MetadataService()
        data_a = ms.resolve_item(kind, id_a)
        data_b = ms.resolve_item(kind, id_b)

        tracks_a = {t.get("title", "").lower().strip(): t for t in data_a.get("tracks", [])}
        tracks_b = {t.get("title", "").lower().strip(): t for t in data_b.get("tracks", [])}

        common_titles = set(tracks_a.keys()) & set(tracks_b.keys())
        only_in_a = [tracks_a[t] for t in (set(tracks_a.keys()) - common_titles)]
        only_in_b = [tracks_b[t] for t in (set(tracks_b.keys()) - common_titles)]

        # Matched tracks with duration diff
        matched_pairs = []
        for t in common_titles:
            ta = tracks_a[t]
            tb = tracks_b[t]
            dur_a = ta.get("duration_ms", 0) / 1000.0
            dur_b = tb.get("duration_ms", 0) / 1000.0
            diff = abs(dur_a - dur_b)
            matched_pairs.append({
                "title": ta.get("title"),
                "duration_a_sec": dur_a,
                "duration_b_sec": dur_b,
                "duration_diff_sec": round(diff, 1),
                "is_identical_duration": diff <= 1.0
            })

        return {
            "release_a": {
                "id": id_a,
                "name": data_a.get("name"),
                "artist": data_a.get("artist"),
                "track_count": len(data_a.get("tracks", [])),
                "cover_url": data_a.get("cover_url")
            },
            "release_b": {
                "id": id_b,
                "name": data_b.get("name"),
                "artist": data_b.get("artist"),
                "track_count": len(data_b.get("tracks", [])),
                "cover_url": data_b.get("cover_url")
            },
            "matched_tracks_count": len(matched_pairs),
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "matched_tracks": matched_pairs
        }

    @classmethod
    def save_provenance(cls, track_id: str, field_name: str, value: str, source: str, confidence: float = 1.0):
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO metadata_provenance (track_id, field_name, value, source, confidence, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(track_id, field_name) DO UPDATE SET "
                "value=excluded.value, source=excluded.source, confidence=excluded.confidence, updated_at=excluded.updated_at",
                (track_id, field_name, str(value), source, confidence, time.time())
            )

    @classmethod
    def get_track_provenance(cls, track_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM metadata_provenance WHERE track_id = ?", (track_id,)).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def compute_tag_diff(cls, file_path: Path, spotify_track_id: str) -> Dict[str, Any]:
        """
        Calculates field-by-field diff between current audio tags and target Spotify metadata.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file '{file_path}' not found")

        ms = MetadataService()
        target_meta = ms.resolve_item("track", spotify_track_id)
        if not target_meta:
            raise ValueError(f"Could not resolve Spotify metadata for '{spotify_track_id}'")

        # Read current tags
        audio = mutagen.File(str(file_path), easy=True)
        current_tags = {}
        if audio is not None:
            for k in ["title", "artist", "album", "date", "tracknumber"]:
                val = audio.get(k)
                current_tags[k] = val[0] if isinstance(val, list) and val else str(val or "")
        else:
            current_tags = {"title": "", "artist": "", "album": "", "date": "", "tracknumber": ""}

        field_comparisons = [
            ("title", current_tags.get("title", ""), target_meta.get("title", "")),
            ("artist", current_tags.get("artist", ""), ", ".join(target_meta.get("artists", [])) if isinstance(target_meta.get("artists"), list) else str(target_meta.get("artists", ""))),
            ("album", current_tags.get("album", ""), target_meta.get("album", "")),
            ("year", current_tags.get("date", ""), str(target_meta.get("year", ""))),
            ("isrc", "", str(target_meta.get("isrc", "")))
        ]

        diffs = []
        for field, current_val, proposed_val in field_comparisons:
            c_clean = (current_val or "").strip()
            p_clean = (proposed_val or "").strip()
            if not c_clean and p_clean:
                diff_type = "ABSENT"
                is_different = True
            elif c_clean and not p_clean:
                diff_type = "EMPTY_PROPOSAL"
                is_different = True
            elif c_clean.lower() != p_clean.lower():
                diff_type = "CONFLICT"
                is_different = True
            else:
                diff_type = "MATCH"
                is_different = False

            diffs.append({
                "field": field,
                "current_value": current_val,
                "proposed_value": proposed_val,
                "status": diff_type,
                "is_different": is_different,
                "source": "spotify_api",
                "confidence": 1.0 if proposed_val else 0.0
            })

        return {
            "file_path": str(file_path),
            "track_id": spotify_track_id,
            "has_changes": any(d["is_different"] for d in diffs),
            "diffs": diffs,
            "target_meta": target_meta
        }

    @classmethod
    def apply_tag_diff(cls, file_path: Path, field_updates: Dict[str, Any], track_id: str = "") -> bool:
        """
        Applies approved field updates onto the target file and records provenance.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file '{file_path}' not found")

        meta = {
            "title": field_updates.get("title"),
            "artists": [field_updates["artist"]] if "artist" in field_updates else None,
            "album": field_updates.get("album"),
            "year": field_updates.get("year"),
            "isrc": field_updates.get("isrc")
        }

        success = TagWriter.apply_tags(file_path, meta)
        if success and track_id:
            for field, val in field_updates.items():
                if val:
                    cls.save_provenance(track_id, field, str(val), "user_approved_diff", 1.0)
            record_audit("METADATA", f"Patched tags for '{file_path.name}' ({track_id})")

        return success

    @classmethod
    def get_metadata_repair_queue(cls) -> List[Dict[str, Any]]:
        """
        Scans local music library for files with missing tags, broken artwork, or missing fields.
        """
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM library_files").fetchall()
        repair_items = []

        for r in rows:
            issues = []
            if not r["title"] or r["title"] == "Unknown":
                issues.append("Missing title")
            if not r["artist"] or r["artist"] == "Unknown":
                issues.append("Missing artist")
            if not r["album"] or r["album"] == "Unknown":
                issues.append("Missing album")

            p = Path(r["path"])
            if not (p.parent / "cover.jpg").exists():
                issues.append("Missing cover art file")

            if issues:
                repair_items.append({
                    "path": r["path"],
                    "filename": r["filename"],
                    "current_title": r["title"],
                    "current_artist": r["artist"],
                    "current_album": r["album"],
                    "issues": issues,
                    "issue_count": len(issues)
                })

        return repair_items
