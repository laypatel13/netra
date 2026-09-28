"""
Multi-frame OCR consensus - aggregate plate readings across evidence frames.

The insight: a single OCR read on a blurry CCTV crop is unreliable.  But if
the same plate is read (even partially) across multiple independent frames,
the consensus is a much stronger signal than any single reading.

This module:
1. Collects OCR readings from multiple evidence frames
2. Groups by plate candidate (normalized)
3. Ranks by support count (how many frames agree) then by confidence
4. Computes a consensus score reflecting agreement strength
5. Preserves ALL candidates - never hides disagreement

IMPORTANT: the result is an "OCR-derived candidate", not guaranteed truth.
The system is an investigative narrowing tool, not autonomous identification.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlateCandidate:
    """A plate candidate with its supporting evidence."""
    plate: str
    supporting_frames: int
    total_frames: int
    confidences: list[float]
    mean_confidence: float
    max_confidence: float


@dataclass
class OCRConsensus:
    """
    Aggregated OCR result across multiple evidence frames.

    Attributes:
        best_plate: most-supported plate candidate (or None)
        supporting_frames: how many frames support the best candidate
        total_frames: total frames where OCR was attempted
        frames_with_plate: how many frames produced any plate reading
        mean_confidence: average OCR confidence for the best candidate
        max_confidence: highest OCR confidence for the best candidate
        consensus_score: 0-1, reflecting how strongly frames agree
        all_candidates: every distinct plate reading, ordered by support
        has_disagreement: True if frames disagree on the plate
    """
    best_plate: Optional[str] = None
    supporting_frames: int = 0
    total_frames: int = 0
    frames_with_plate: int = 0
    mean_confidence: float = 0.0
    max_confidence: float = 0.0
    consensus_score: float = 0.0
    all_candidates: list[PlateCandidate] = field(default_factory=list)
    has_disagreement: bool = False


@dataclass
class FrameOCRResult:
    """OCR result from a single frame - input to consensus."""
    frame_index: int
    plate: Optional[str]     # None if no plate found
    confidence: float        # 0 if no plate


def _normalize_plate(plate: str) -> str:
    """Normalize for comparison - uppercase, no spaces/dashes."""
    import re
    return re.sub(r"[^A-Z0-9]", "", plate.strip().upper())


def compute_consensus(readings: list[FrameOCRResult]) -> OCRConsensus:
    """
    Aggregate OCR across multiple frames and compute consensus.

    Algorithm:
    1. Group readings by normalized plate text
    2. Count support for each candidate
    3. Best candidate = most frames supporting it (ties broken by confidence)
    4. Consensus score reflects:
       - What fraction of plate-reading frames agree on the best candidate
       - Weighted by average confidence
    5. Disagreement is preserved in all_candidates

    Returns OCRConsensus with full breakdown.
    """
    total_frames = len(readings)
    if total_frames == 0:
        return OCRConsensus(total_frames=0)

    # Separate frames with and without plate readings
    plate_readings = [r for r in readings if r.plate]
    frames_with_plate = len(plate_readings)

    if frames_with_plate == 0:
        return OCRConsensus(
            total_frames=total_frames,
            frames_with_plate=0,
            consensus_score=0.0,
        )

    # Group by normalized plate
    plate_groups: dict[str, list[FrameOCRResult]] = {}
    for r in plate_readings:
        key = _normalize_plate(r.plate)
        if key not in plate_groups:
            plate_groups[key] = []
        plate_groups[key].append(r)

    # Build candidates
    candidates = []
    for plate, group in plate_groups.items():
        confs = [r.confidence for r in group]
        candidates.append(PlateCandidate(
            plate=plate,
            supporting_frames=len(group),
            total_frames=total_frames,
            confidences=confs,
            mean_confidence=round(sum(confs) / len(confs), 4),
            max_confidence=round(max(confs), 4),
        ))

    # Sort: most support first, then by mean confidence
    candidates.sort(key=lambda c: (c.supporting_frames, c.mean_confidence), reverse=True)

    best = candidates[0]
    has_disagreement = len(candidates) > 1

    # Consensus score:
    # - Base: fraction of plate-reading frames that agree on best candidate
    # - Weighted by mean confidence of the best candidate
    # - Penalized slightly if there's disagreement
    agreement_ratio = best.supporting_frames / frames_with_plate
    confidence_factor = best.mean_confidence
    disagreement_penalty = 0.9 if has_disagreement else 1.0

    consensus_score = round(
        agreement_ratio * confidence_factor * disagreement_penalty,
        4,
    )

    return OCRConsensus(
        best_plate=best.plate,
        supporting_frames=best.supporting_frames,
        total_frames=total_frames,
        frames_with_plate=frames_with_plate,
        mean_confidence=best.mean_confidence,
        max_confidence=best.max_confidence,
        consensus_score=consensus_score,
        all_candidates=candidates,
        has_disagreement=has_disagreement,
    )


def character_vote(plates: list[str]) -> Optional[str]:
    """
    Character-level voting across plate candidates.

    When plates partially disagree (e.g. GJ01AB1234 vs GJ01AB1284),
    vote on each character position independently.  Only works when
    all candidates have the same length.

    Returns the voted plate, or None if lengths differ.
    """
    if not plates:
        return None

    lengths = set(len(p) for p in plates)
    if len(lengths) != 1:
        return None  # different lengths - can't vote character-by-character

    length = lengths.pop()
    voted = []
    for i in range(length):
        chars = [p[i] for p in plates]
        most_common = Counter(chars).most_common(1)[0][0]
        voted.append(most_common)

    return "".join(voted)
