"""
Cheap candidate filtering against investigation targets.

Before expensive multi-frame processing (OCR, enhancement), this module
performs a fast attribute-compatibility check to decide whether a detected
vehicle is worth investigating further.

Uses a three-valued logic (MATCH / NON_MATCH / UNKNOWN) rather than forcing
binary decisions - a vehicle with an uncertain color is not discarded, it's
marked UNKNOWN and still enters the investigation pipeline.  Only vehicles
with a clear NON_MATCH on a non-nullable target attribute are rejected.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MatchResult(str, Enum):
    MATCH = "match"
    APPROXIMATE = "approximate"
    NON_MATCH = "non_match"
    UNKNOWN = "unknown"

@dataclass
class FilterResult:
    """Result of filtering a candidate against a target."""
    type_match: MatchResult
    color_match: MatchResult
    plate_match: MatchResult
    should_investigate: bool
    detail: str = ""

def _normalize_color(color: Optional[str]) -> Optional[str]:
    """Normalize color for comparison - lowercase, stripped."""
    if not color:
        return None
    c = color.strip().lower()
    if c == "unknown":
        return None
    return c

def _normalize_type(vtype: Optional[str]) -> Optional[str]:
    """Normalize vehicle type for comparison."""
    if not vtype:
        return None
    t = vtype.strip().lower()
    if t == "unknown":
        return None
    return t

def _normalize_plate(plate: Optional[str]) -> Optional[str]:
    """Normalize plate for comparison - uppercase, no spaces/dashes."""
    if not plate:
        return None
    import re
    return re.sub(r"[^A-Z0-9]", "", plate.strip().upper())

def _compare(target_value: Optional[str], candidate_value: Optional[str]) -> MatchResult:
    """Compare a single attribute. None on either side → UNKNOWN."""
    if target_value is None or candidate_value is None:
        return MatchResult.UNKNOWN
    if target_value == candidate_value:
        return MatchResult.MATCH
    return MatchResult.NON_MATCH

def _compare_color(target_value: Optional[str], candidate_value: Optional[str]) -> MatchResult:
    if target_value is None or candidate_value is None:
        return MatchResult.UNKNOWN
    
    # Direct match
    if target_value == candidate_value:
        return MatchResult.MATCH
        
    # Color family rules
    color_families = {
        "red": {"red", "orange", "maroon"},
        "blue": {"blue", "cyan", "navy"},
        "white": {"white", "silver_gray"},
        "black": {"black", "dark_gray"}
    }
    
    if target_value in color_families and candidate_value in color_families[target_value]:
        # Explicitly distinguish approximate from exact
        return MatchResult.APPROXIMATE
        
    return MatchResult.NON_MATCH

def filter_candidate(
    target_plate: Optional[str],
    target_type: Optional[str],
    target_color: Optional[str],
    candidate_type: Optional[str],
    candidate_color: Optional[str],
    candidate_plate: Optional[str] = None,
    type_required: bool = False,
    color_required: bool = False,
) -> FilterResult:
    """
    Check whether a detected vehicle is compatible with an investigation target.

    A candidate should enter the investigation pipeline unless it has a clear
    NON_MATCH on a target attribute that was specified.  The principle:
    don't discard a vehicle solely because one coarse classifier is uncertain.
    """
    t_plate = _normalize_plate(target_plate)
    t_type = _normalize_type(target_type)
    t_color = _normalize_color(target_color)

    c_type = _normalize_type(candidate_type)
    c_color = _normalize_color(candidate_color)
    c_plate = _normalize_plate(candidate_plate)

    plate_match = _compare(t_plate, c_plate)
    type_match = _compare(t_type, c_type)
    color_match = _compare_color(t_color, c_color)

    # An exact plate match immediately qualifies
    if plate_match == MatchResult.MATCH:
        return FilterResult(
            type_match=type_match,
            color_match=color_match,
            plate_match=plate_match,
            should_investigate=True,
            detail="Exact plate match",
        )

    # A clear plate NON_MATCH when both sides have plates → reject
    if plate_match == MatchResult.NON_MATCH:
        return FilterResult(
            type_match=type_match,
            color_match=color_match,
            plate_match=plate_match,
            should_investigate=False,
            detail="Plate mismatch",
        )

    # Check attribute compatibility - reject ONLY on clear non-match.
    # UNKNOWN + Required is NOT a non-match; it's insufficient evidence (handled by scoring layer).
    non_matches = []
    
    if type_match == MatchResult.NON_MATCH:
        non_matches.append("type")
        
    if color_match == MatchResult.NON_MATCH:
        non_matches.append("color")

    if non_matches:
        return FilterResult(
            type_match=type_match,
            color_match=color_match,
            plate_match=plate_match,
            should_investigate=False,
            detail=f"Attribute mismatch: {', '.join(non_matches)}",
        )

    # No clear disqualifiers → investigate
    matches = []
    if type_match == MatchResult.MATCH:
        matches.append("type")
    if color_match == MatchResult.MATCH:
        matches.append("color")

    detail = f"Compatible ({', '.join(matches) if matches else 'no conflicts'})"
    return FilterResult(
        type_match=type_match,
        color_match=color_match,
        plate_match=plate_match,
        should_investigate=True,
        detail=detail,
    )
