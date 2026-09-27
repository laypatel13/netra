"""
Evidence fusion scoring — transparent, configurable, explainable.

Combines plate match, OCR consensus, vehicle type/color match, image quality,
and temporal consistency into a single candidate score with full breakdown.

All weights are module-level constants, easily changed.  The API returns
component scores alongside the final score — nothing is hidden inside an
opaque model.

CRITICAL: uses conditional weighted scoring.  Only signals where real
evidence exists contribute to the score.  Missing OCR does not penalize a
candidate — instead, evidence_completeness tells the operator how much of
the expected evidence is actually available.

Match tiers:
    EXACT_PLATE         Validated plate matches the target
    STRONG_CANDIDATE    Multiple signals agree, score >= threshold
    ATTRIBUTE_CANDIDATE Only coarse attributes match
    NO_MATCH            Insufficient compatibility

IMPORTANT: even EXACT_PLATE is phrased as a system match, not legal identity
confirmation.  Human verification is always required.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# --- Configurable weights ---
WEIGHT_PLATE = 0.35
WEIGHT_OCR_CONSENSUS = 0.25
WEIGHT_TYPE = 0.15
WEIGHT_COLOR = 0.10
WEIGHT_QUALITY = 0.10
WEIGHT_TEMPORAL = 0.05

# --- Tier thresholds ---
STRONG_CANDIDATE_THRESHOLD = 0.70
ATTRIBUTE_CANDIDATE_THRESHOLD = 0.30


class MatchTier(str, Enum):
    EXACT_PLATE = "exact_plate"
    STRONG_CANDIDATE = "strong_candidate"
    ATTRIBUTE_CANDIDATE = "attribute_candidate"
    NO_MATCH = "no_match"


# Human-readable tier labels for the UI
TIER_LABELS = {
    MatchTier.EXACT_PLATE: "Plate match — human verification required",
    MatchTier.STRONG_CANDIDATE: "High-confidence candidate — human verification required",
    MatchTier.ATTRIBUTE_CANDIDATE: "Potential vehicle — description-based narrowing only",
    MatchTier.NO_MATCH: "No match",
}


@dataclass
class SignalScores:
    """Individual signal scores, all in [0, 1]."""
    plate: float = 0.0
    ocr_consensus: float = 0.0
    vehicle_type: float = 0.0
    vehicle_color: float = 0.0
    image_quality: float = 0.0
    temporal_consistency: float = 0.0

    def to_dict(self) -> dict:
        return {
            "plate": round(self.plate, 4),
            "ocr_consensus": round(self.ocr_consensus, 4),
            "vehicle_type": round(self.vehicle_type, 4),
            "vehicle_color": round(self.vehicle_color, 4),
            "image_quality": round(self.image_quality, 4),
            "temporal_consistency": round(self.temporal_consistency, 4),
        }


@dataclass
class EvidenceScore:
    """Final evidence fusion result with full breakdown."""
    final_score: float
    tier: MatchTier
    tier_label: str
    signals: SignalScores
    explanation: str
    evidence_frame_count: int = 0
    ocr_candidates_found: int = 0
    evidence_completeness: float = 0.0

    def to_dict(self) -> dict:
        return {
            "final_score": round(self.final_score, 4),
            "tier": self.tier.value,
            "tier_label": self.tier_label,
            "signals": self.signals.to_dict(),
            "explanation": self.explanation,
            "evidence_frame_count": self.evidence_frame_count,
            "ocr_candidates_found": self.ocr_candidates_found,
            "evidence_completeness": round(self.evidence_completeness, 4),
        }


def _plate_signal(
    target_plate: Optional[str],
    ocr_best_plate: Optional[str],
    ocr_consensus_score: float,
) -> tuple[float, float]:
    """
    Compute plate match score and OCR consensus score.

    Returns (plate_score, ocr_score).
    """
    if not target_plate:
        # No target plate specified — these signals don't apply
        return 0.0, ocr_consensus_score

    if not ocr_best_plate:
        # Target has a plate but OCR found nothing
        return 0.0, 0.0

    # Normalize for comparison
    import re
    t = re.sub(r"[^A-Z0-9]", "", target_plate.strip().upper())
    o = re.sub(r"[^A-Z0-9]", "", ocr_best_plate.strip().upper())

    if t == o:
        return 1.0, ocr_consensus_score

    # Partial match — count matching characters
    if len(t) == len(o) and len(t) > 0:
        matching = sum(1 for a, b in zip(t, o) if a == b)
        partial = matching / len(t)
        return partial, ocr_consensus_score * partial

    return 0.0, ocr_consensus_score * 0.5


def _type_signal(
    target_type: Optional[str],
    detected_type: Optional[str],
) -> float:
    """Vehicle type match — 1.0 for match, 0.0 for mismatch, 0.5 for unknown."""
    if not target_type or not detected_type:
        return 0.5  # unknown — neutral
    if target_type.strip().lower() == detected_type.strip().lower():
        return 1.0
    return 0.0


def _color_signal(
    target_color: Optional[str],
    detected_color: Optional[str],
) -> float:
    """Vehicle color match — 1.0 for match, 0.8 for family match, 0.0 for mismatch, 0.5 for unknown."""
    if not target_color or not detected_color:
        return 0.5
        
    t_color = target_color.strip().lower()
    d_color = detected_color.strip().lower()
    
    if t_color == d_color:
        return 1.0
        
    color_families = {
        "red": {"red", "orange", "maroon"},
        "blue": {"blue", "cyan", "navy"},
        "white": {"white", "silver_gray"},
        "black": {"black", "dark_gray"}
    }
    
    if t_color in color_families and d_color in color_families[t_color]:
        return 0.8
        
    return 0.0


def _temporal_consistency_signal(
    type_readings: Optional[list[str]] = None,
    color_readings: Optional[list[str]] = None,
    ocr_readings: Optional[list[Optional[str]]] = None,
) -> float:
    """
    Measure actual attribute consistency across observations — NOT frame count.

    A vehicle appearing for 20 frames does not automatically mean stronger match.
    Instead, measure whether type/color/OCR readings are consistent across frames.
    """
    scores = []

    # Type consistency
    if type_readings and len(type_readings) > 0:
        from collections import Counter
        most_common_count = Counter(type_readings).most_common(1)[0][1]
        scores.append(most_common_count / len(type_readings))

    # Color consistency
    if color_readings:
        non_null = [c for c in color_readings if c]
        if non_null:
            from collections import Counter
            most_common_count = Counter(non_null).most_common(1)[0][1]
            scores.append(most_common_count / len(non_null))

    # OCR consistency
    if ocr_readings:
        non_null = [o for o in ocr_readings if o]
        if len(non_null) >= 2:
            from collections import Counter
            most_common_count = Counter(non_null).most_common(1)[0][1]
            scores.append(most_common_count / len(non_null))

    if not scores:
        return 0.2  # minimal evidence

    return sum(scores) / len(scores)


def compute_evidence_score(
    target_plate: Optional[str],
    target_type: Optional[str],
    target_color: Optional[str],
    ocr_best_plate: Optional[str],
    ocr_consensus_score: float,
    detected_type: Optional[str],
    detected_color: Optional[str],
    image_quality: float,
    evidence_frame_count: int,
    ocr_candidates_found: int = 0,
    type_readings: Optional[list[str]] = None,
    color_readings: Optional[list[str]] = None,
    ocr_readings: Optional[list[Optional[str]]] = None,
    color_unknown_reason: Optional[str] = None,
    type_required: bool = False,
    color_required: bool = False,
) -> EvidenceScore:
    """
    Compute the final evidence fusion score using CONDITIONAL WEIGHTING.

    Only signals where real evidence exists contribute to the score.
    Missing OCR does NOT penalize a candidate.  Instead, evidence_completeness
    tells the operator how much of the expected evidence is actually available.
    """
    plate_score, ocr_score = _plate_signal(
        target_plate, ocr_best_plate, ocr_consensus_score
    )
    type_score = _type_signal(target_type, detected_type)
    color_score = _color_signal(target_color, detected_color)
    temporal_score = _temporal_consistency_signal(type_readings, color_readings, ocr_readings)

    signals = SignalScores(
        plate=plate_score,
        ocr_consensus=ocr_score,
        vehicle_type=type_score,
        vehicle_color=color_score,
        image_quality=image_quality,
        temporal_consistency=temporal_score,
    )

    # --- Conditional weighted scoring ---
    # Only include signals where we have real evidence.
    # Don't penalize missing OCR/plate with a zero score.
    weighted_sum = 0.0
    available_weight = 0.0
    total_possible_weight = 0.0

    # Plate signal: only include if target has a plate AND we got OCR results
    if target_plate:
        total_possible_weight += WEIGHT_PLATE
        if ocr_best_plate:
            weighted_sum += WEIGHT_PLATE * plate_score
            available_weight += WEIGHT_PLATE
    
    # OCR consensus: only include if we actually got OCR candidates
    if target_plate:
        total_possible_weight += WEIGHT_OCR_CONSENSUS
        if ocr_candidates_found > 0:
            weighted_sum += WEIGHT_OCR_CONSENSUS * ocr_score
            available_weight += WEIGHT_OCR_CONSENSUS

    # Type signal: always available (YOLO always produces a type)
    if target_type:
        total_possible_weight += WEIGHT_TYPE
        if detected_type and detected_type != "unknown":
            weighted_sum += WEIGHT_TYPE * type_score
            available_weight += WEIGHT_TYPE
    elif detected_type and detected_type != "unknown":
        # No target type specified, include at neutral weight
        total_possible_weight += WEIGHT_TYPE
        weighted_sum += WEIGHT_TYPE * 0.5
        available_weight += WEIGHT_TYPE

    # Color signal: include if we have a detected color
    if target_color:
        total_possible_weight += WEIGHT_COLOR
        if detected_color and detected_color != "unknown":
            weighted_sum += WEIGHT_COLOR * color_score
            available_weight += WEIGHT_COLOR
    elif detected_color and detected_color != "unknown":
        total_possible_weight += WEIGHT_COLOR
        weighted_sum += WEIGHT_COLOR * 0.5
        available_weight += WEIGHT_COLOR

    # Quality: always available
    total_possible_weight += WEIGHT_QUALITY
    weighted_sum += WEIGHT_QUALITY * image_quality
    available_weight += WEIGHT_QUALITY

    # Temporal: always available if we have any frames
    if evidence_frame_count > 0:
        total_possible_weight += WEIGHT_TEMPORAL
        weighted_sum += WEIGHT_TEMPORAL * temporal_score
        available_weight += WEIGHT_TEMPORAL

    # Normalize by available weight
    if available_weight > 0:
        final = weighted_sum / available_weight
    else:
        final = 0.0

    final = round(min(max(final, 0.0), 1.0), 4)

    # Evidence completeness: ratio of available to total possible
    if total_possible_weight > 0:
        evidence_completeness = round(available_weight / total_possible_weight, 4)
    else:
        evidence_completeness = 0.0

    # If a required attribute explicitly contradicts the target, it's a NO_MATCH.
    has_type_contradiction = bool(target_type and detected_type and detected_type != "unknown" and type_score == 0.0)
    has_color_contradiction = bool(target_color and detected_color and detected_color != "unknown" and color_score == 0.0)
    
    # STRICT MATCH GATING:
    # If the user explicitly checks "Require exact match" in the UI, we must discard "unknown"
    if type_required and (not detected_type or detected_type == "unknown"):
        has_type_contradiction = True
    if color_required and (not detected_color or detected_color == "unknown"):
        has_color_contradiction = True
    
    if has_type_contradiction or has_color_contradiction:
        final = 0.0
        tier = MatchTier.NO_MATCH
    else:
        # EXACT PLATE OVERRIDE: cannot be downgraded by weighted score
        if plate_score >= 0.95 and target_plate and ocr_consensus_score >= 0.5:
            tier = MatchTier.EXACT_PLATE
        elif final >= STRONG_CANDIDATE_THRESHOLD:
            tier = MatchTier.STRONG_CANDIDATE
        elif final >= ATTRIBUTE_CANDIDATE_THRESHOLD:
            tier = MatchTier.ATTRIBUTE_CANDIDATE
        else:
            tier = MatchTier.NO_MATCH

    # Build explanation
    explanations = []
    if has_type_contradiction:
        explanations.append(f"Explicit type mismatch (wanted {target_type}, saw {detected_type})")
    if has_color_contradiction:
        explanations.append(f"Explicit color mismatch (wanted {target_color}, saw {detected_color})")
        
    if not has_type_contradiction and not has_color_contradiction:
        if plate_score >= 0.95 and target_plate:
            explanations.append(f"Plate '{ocr_best_plate}' matches target — human verification required")
        elif plate_score > 0 and target_plate:
            explanations.append(f"Partial plate match ({plate_score:.0%})")
        if type_score == 1.0:
            explanations.append("Vehicle type matches")
        if color_score == 1.0:
            explanations.append("Vehicle color matches")
        if ocr_score > 0.5:
            explanations.append(f"OCR consensus: {ocr_score:.0%}")
        if evidence_frame_count > 1:
            explanations.append(f"{evidence_frame_count} evidence frames")
            
    if evidence_completeness < 0.5:
        explanations.append(f"Limited evidence ({evidence_completeness:.0%} completeness)")
        
    if detected_color == "unknown" and color_unknown_reason:
        explanations.append(f"Color unknown ({color_unknown_reason})")

    return EvidenceScore(
        final_score=final,
        tier=tier,
        tier_label=TIER_LABELS[tier],
        signals=signals,
        explanation="; ".join(explanations) if explanations else "Insufficient evidence",
        evidence_frame_count=evidence_frame_count,
        ocr_candidates_found=ocr_candidates_found,
        evidence_completeness=evidence_completeness,
    )
