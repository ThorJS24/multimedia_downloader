import difflib
import logging
import re
from typing import Any, Dict, List, Tuple

from src.providers.base import MatchOutcome, NormalizedItem

logger = logging.getLogger("CrossSourceMatchingService")


class CrossSourceMatchingService:
    """
    Conservative, explainable cross-source audio matching engine.
    Compares candidate recordings from YouTube Music and YouTube against
    a target recording (e.g., Spotify track), strictly preventing false positives,
    variant confusion (live, acoustic, remix, cover), and silent substitution.
    """

    VARIANT_KEYWORDS = {
        "live": re.compile(r"\b(live|in concert|recorded at|tour|unplugged)\b", re.IGNORECASE),
        "acoustic": re.compile(r"\b(acoustic|unplugged|piano version)\b", re.IGNORECASE),
        "remix": re.compile(r"\b(remix|club mix|extended mix|dub|mashup)\b", re.IGNORECASE),
        "instrumental": re.compile(r"\b(instrumental|karaoke|backing track)\b", re.IGNORECASE),
        "remaster": re.compile(r"\b(remaster|remastered|anniversary|reissue)\b", re.IGNORECASE),
        "cover": re.compile(r"\b(cover|tribute|style of|originally by)\b", re.IGNORECASE),
        "clean": re.compile(r"\b(clean|radio edit)\b", re.IGNORECASE),
        "explicit": re.compile(r"\b(explicit)\b", re.IGNORECASE),
    }

    @staticmethod
    def normalize_string(s: str) -> str:
        if not s:
            return ""
        s = s.lower().strip()
        # Remove parenthetical video/audio extras
        s = re.sub(r"[\(\[][^\)\]]*(official|video|audio|lyrics|hd|4k|mv|visualizer)[^\)\]]*[\)\]]", "", s)
        # Strip non-alphanumeric except spaces
        s = re.sub(r"[^\w\s]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    @classmethod
    def detect_variants(cls, text: str) -> Dict[str, bool]:
        return {
            k: bool(regex.search(text))
            for k, regex in cls.VARIANT_KEYWORDS.items()
        }

    @classmethod
    def evaluate(
        cls,
        target: NormalizedItem,
        candidate: NormalizedItem,
        duration_tolerance_sec: float = 4.0
    ) -> Tuple[MatchOutcome, float, str, Dict[str, Any]]:
        """
        Evaluates candidate against target.
        Returns (MatchOutcome, confidence_score, rationale, breakdown).
        """
        breakdown: Dict[str, Any] = {}
        reasons: List[str] = []

        # 1. Exact ISRC check
        t_isrc = (target.isrc or "").strip().upper()
        c_isrc = (candidate.isrc or "").strip().upper()
        if t_isrc and c_isrc and t_isrc == c_isrc:
            breakdown["isrc_match"] = True
            return (
                MatchOutcome.EXACT_IDENTIFIER_MATCH,
                1.0,
                f"Exact ISRC recording match ({t_isrc})",
                breakdown
            )

        # 2. Variant / Version mismatch check (CRITICAL for fidelity)
        t_full = f"{target.title} {target.album or ''}"
        c_full = f"{candidate.title} {candidate.album or ''}"
        t_vars = cls.detect_variants(t_full)
        c_vars = cls.detect_variants(c_full)

        variant_conflicts = []
        for var_key, c_present in c_vars.items():
            t_present = t_vars[var_key]
            if c_present != t_present:
                variant_conflicts.append(f"Candidate {var_key} mismatch (Target={t_present}, Candidate={c_present})")

        if variant_conflicts:
            breakdown["variant_conflicts"] = variant_conflicts
            return (
                MatchOutcome.NO_MATCH,
                0.2,
                f"Variant mismatch detected: {', '.join(variant_conflicts)}",
                breakdown
            )

        # 3. String similarities
        norm_t_title = cls.normalize_string(target.title)
        norm_c_title = cls.normalize_string(candidate.title)
        title_sim = difflib.SequenceMatcher(None, norm_t_title, norm_c_title).ratio()
        breakdown["title_similarity"] = round(title_sim, 2)

        norm_t_artist = cls.normalize_string(target.primary_creator)
        norm_c_artist = cls.normalize_string(candidate.primary_creator)
        artist_sim = difflib.SequenceMatcher(None, norm_t_artist, norm_c_artist).ratio()

        # Artist substring check (e.g. channel name contains artist name or vice versa)
        if norm_t_artist in norm_c_artist or norm_c_artist in norm_t_artist or norm_t_artist in norm_c_title:
            artist_sim = max(artist_sim, 0.90)
        breakdown["artist_similarity"] = round(artist_sim, 2)

        # Immediate rejection if title similarity is too low or artist completely dissimilar
        if title_sim < 0.45 or artist_sim < 0.40:
            return (
                MatchOutcome.NO_MATCH,
                0.1,
                f"Low title or artist similarity (title={title_sim:.2f}, artist={artist_sim:.2f})",
                breakdown
            )

        # 4. Duration comparison
        dur_score = 1.0
        dur_diff_sec = 0.0
        if target.duration_ms and candidate.duration_ms:
            t_sec = target.duration_ms / 1000.0
            c_sec = candidate.duration_ms / 1000.0
            dur_diff_sec = abs(t_sec - c_sec)
            breakdown["duration_diff_sec"] = round(dur_diff_sec, 1)

            if dur_diff_sec > 30.0:
                return (
                    MatchOutcome.NO_MATCH,
                    0.25,
                    f"Severe duration divergence: {dur_diff_sec:.1f}s difference (> 30s limit)",
                    breakdown
                )
            elif dur_diff_sec > 10.0:
                dur_score = 0.50
                reasons.append(f"Moderate duration divergence ({dur_diff_sec:.1f}s)")
            elif dur_diff_sec > duration_tolerance_sec:
                dur_score = 0.80
                reasons.append(f"Minor duration divergence ({dur_diff_sec:.1f}s)")
            else:
                dur_score = 1.0
                reasons.append("Duration matches within tolerance")

        # 5. Composite Confidence Score Calculation
        composite = (title_sim * 0.50) + (artist_sim * 0.30) + (dur_score * 0.20)
        breakdown["composite_score"] = round(composite, 2)

        # Determine Outcome Category
        if composite >= 0.88 and dur_diff_sec <= 4.0:
            outcome = MatchOutcome.HIGH_CONFIDENCE_CANDIDATE
            rationale = f"High confidence candidate match (Score: {composite:.2f}, Title: {title_sim:.2f}, Artist: {artist_sim:.2f})"
        elif composite >= 0.65:
            outcome = MatchOutcome.POSSIBLE_MATCH
            rationale = f"Possible candidate match requiring user review (Score: {composite:.2f})"
        else:
            outcome = MatchOutcome.NO_MATCH
            rationale = f"Insufficient match confidence ({composite:.2f} < 0.65)"

        return outcome, round(composite, 2), rationale, breakdown

    @classmethod
    def rank_candidates(
        cls,
        target: NormalizedItem,
        candidates: List[NormalizedItem]
    ) -> List[Dict[str, Any]]:
        """
        Evaluates and ranks a list of candidates against the target item.
        Detects ambiguous outcomes when multiple candidates have near-identical confidence.
        """
        evaluated: List[Dict[str, Any]] = []
        for cand in candidates:
            outcome, score, rationale, breakdown = cls.evaluate(target, cand)
            cand.match_status = outcome.value
            evaluated.append({
                "candidate": cand.to_dict(),
                "outcome": outcome.value,
                "confidence_score": score,
                "rationale": rationale,
                "breakdown": breakdown
            })

        # Sort descending by score
        evaluated.sort(key=lambda x: x["confidence_score"], reverse=True)

        # Check for ambiguity: if top 2 candidates are both above 0.70 and within 0.05 of each other
        if len(evaluated) >= 2:
            top1 = evaluated[0]
            top2 = evaluated[1]
            if top1["confidence_score"] >= 0.70 and abs(top1["confidence_score"] - top2["confidence_score"]) <= 0.05:
                top1["outcome"] = MatchOutcome.AMBIGUOUS.value
                top1["rationale"] = f"Ambiguous match: multiple plausible candidates ({top1['confidence_score']} vs {top2['confidence_score']})"
                top2["outcome"] = MatchOutcome.AMBIGUOUS.value

        return evaluated
