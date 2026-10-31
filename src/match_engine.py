import difflib
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger("MatchEngine")


class MatchCandidate:
    def __init__(
        self,
        candidate_id: str,
        title: str,
        artists: List[str],
        duration_ms: int,
        source: str = "Spotify AP CDN",
        isrc: str = "",
        is_live: bool = False,
        is_remix: bool = False
    ):
        self.candidate_id = candidate_id
        self.title = title
        self.artists = artists
        self.duration_ms = duration_ms
        self.source = source
        self.isrc = isrc
        self.is_live = is_live
        self.is_remix = is_remix
        self.confidence_score = 0.0
        self.is_acceptable = False
        self.match_reasons: List[str] = []
        self.decision_breakdown: Dict[str, Any] = {}

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "artists": self.artists,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "isrc": self.isrc,
            "confidence_score": round(self.confidence_score, 2),
            "is_acceptable": self.is_acceptable,
            "match_reasons": self.match_reasons,
            "decision_breakdown": self.decision_breakdown
        }


class MatchEngine:
    """
    Intelligent audio matching engine. Evaluates track candidates against
    target metadata, scoring title, artist, duration, ISRC, and version tags.
    Ensures zero silent substitutions when match confidence is insufficient.
    """

    VARIANT_KEYWORDS = {
        "live": re.compile(r"\b(live|in concert|recorded at|tour|unplugged)\b", re.IGNORECASE),
        "acoustic": re.compile(r"\b(acoustic|unplugged)\b", re.IGNORECASE),
        "remix": re.compile(r"\b(remix|club mix|extended mix|dub|mashup)\b", re.IGNORECASE),
        "instrumental": re.compile(r"\b(instrumental|karaoke|backing track)\b", re.IGNORECASE),
        "remaster": re.compile(r"\b(remaster|remastered|anniversary|deluxe|reissue)\b", re.IGNORECASE),
        "edit": re.compile(r"\b(radio edit|clean|explicit|single version)\b", re.IGNORECASE),
    }

    @staticmethod
    def normalize_string(s: str) -> str:
        s = s.lower().strip()
        # Remove parenthetical video/audio extras like "(official audio)" or "[4K]"
        s = re.sub(r"[\(\[][^\)\]]*(official|video|audio|lyrics|hd|4k)[^\)\]]*[\)\]]", "", s)
        s = re.sub(r"[^\w\s]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    @classmethod
    def detect_variants(cls, text: str) -> Dict[str, bool]:
        return {
            k: bool(regex.search(text))
            for k, regex in cls.VARIANT_KEYWORDS.items()
        }

    @classmethod
    def evaluate_match(
        cls,
        expected: dict,
        candidate: MatchCandidate,
        duration_tolerance_sec: float = 4.0,
        min_threshold: float = 0.70
    ) -> float:
        score = 0.0
        reasons = []
        breakdown: Dict[str, Any] = {}

        # 1. ISRC exact match (100% verified identifier)
        exp_isrc = expected.get("isrc", "").strip().upper()
        cand_isrc = candidate.isrc.strip().upper()
        if exp_isrc and cand_isrc and exp_isrc == cand_isrc:
            candidate.confidence_score = 1.0
            candidate.is_acceptable = True
            candidate.match_reasons = ["Exact ISRC match (100% verified unique identifier)"]
            candidate.decision_breakdown = {
                "isrc_match": True,
                "title_score": 1.0,
                "artist_score": 1.0,
                "duration_delta_sec": 0.0,
                "variant_penalty": 0.0,
                "final_confidence": 1.0
            }
            return 1.0

        breakdown["isrc_match"] = False

        # 2. Title similarity (up to 45% weight)
        exp_title = cls.normalize_string(expected.get("title", ""))
        cand_title = cls.normalize_string(candidate.title)
        title_sim = difflib.SequenceMatcher(None, exp_title, cand_title).ratio()
        title_weighted = title_sim * 0.45
        score += title_weighted
        reasons.append(f"Title similarity: {int(title_sim * 100)}% (+{title_weighted:.2f})")
        breakdown["title_similarity"] = round(title_sim, 2)

        # 3. Artist similarity (up to 35% weight)
        exp_artists = expected.get("artists", [""])
        exp_artist_str = exp_artists[0] if isinstance(exp_artists, list) and exp_artists else str(exp_artists)
        cand_artist_str = candidate.artists[0] if candidate.artists else ""
        exp_norm_art = cls.normalize_string(exp_artist_str)
        cand_norm_art = cls.normalize_string(cand_artist_str)

        artist_sim = difflib.SequenceMatcher(None, exp_norm_art, cand_norm_art).ratio()
        artist_weighted = artist_sim * 0.35
        score += artist_weighted
        reasons.append(f"Artist similarity: {int(artist_sim * 100)}% (+{artist_weighted:.2f})")
        breakdown["artist_similarity"] = round(artist_sim, 2)

        # 4. Duration comparison (up to 20% weight, with penalties for large deltas)
        exp_dur = expected.get("duration_ms", 0) / 1000.0
        cand_dur = candidate.duration_ms / 1000.0
        diff = abs(exp_dur - cand_dur)
        breakdown["duration_delta_sec"] = round(diff, 1)

        if diff <= 1.5:
            score += 0.20
            reasons.append(f"Duration matches exact length (diff: {diff:.1f}s, +0.20)")
        elif diff <= duration_tolerance_sec:
            partial_dur = max(0.05, 0.20 - (diff / duration_tolerance_sec) * 0.10)
            score += partial_dur
            reasons.append(f"Duration within tolerance (diff: {diff:.1f}s, +{partial_dur:.2f})")
        else:
            penalty = min(0.30, (diff / 10.0) * 0.20)
            score -= penalty
            reasons.append(f"Duration mismatch penalty (diff: {diff:.1f}s, -{penalty:.2f})")

        # 5. Audio Variant Analysis & Penalties
        exp_text = f"{expected.get('title', '')} {expected.get('album', '')}"
        cand_text = candidate.title
        exp_variants = cls.detect_variants(exp_text)
        cand_variants = cls.detect_variants(cand_text)

        variant_penalties = 0.0
        for variant_type in ["live", "acoustic", "remix", "instrumental"]:
            if exp_variants[variant_type] != cand_variants[variant_type]:
                pen = 0.25
                variant_penalties += pen
                score -= pen
                state = "Candidate is " if cand_variants[variant_type] else "Candidate is NOT "
                reasons.append(f"Variant mismatch: {state}{variant_type.upper()} (-{pen})")

        breakdown["variant_penalty"] = round(variant_penalties, 2)

        final_score = max(0.0, min(1.0, score))
        candidate.confidence_score = final_score
        candidate.is_acceptable = final_score >= min_threshold
        candidate.match_reasons = reasons
        breakdown["final_confidence"] = round(final_score, 2)
        breakdown["is_acceptable"] = candidate.is_acceptable
        candidate.decision_breakdown = breakdown

        if not candidate.is_acceptable:
            reasons.append(f"Match rejected: Confidence {final_score:.2f} is below minimum threshold {min_threshold:.2f}")

        return candidate.confidence_score

    @classmethod
    def explain_match(cls, expected: dict, candidate: MatchCandidate, min_threshold: float = 0.70) -> dict:
        cls.evaluate_match(expected, candidate, min_threshold=min_threshold)
        return {
            "candidate_id": candidate.candidate_id,
            "title": candidate.title,
            "artists": candidate.artists,
            "confidence_score": round(candidate.confidence_score, 2),
            "is_acceptable": candidate.is_acceptable,
            "reasons": candidate.match_reasons,
            "breakdown": candidate.decision_breakdown
        }
