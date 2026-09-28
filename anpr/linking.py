"""
Cross-Camera Observation Linking Engine.

Responsible for comparing two VehicleObservations across a CameraGraphEdge to 
determine if they could represent the same vehicle.

Principles:
1. UNKNOWN is NOT MATCH.
2. Missing evidence is different from contradictory evidence.
3. Do NOT hide contradictions.
4. Temporal feasibility is strict based on edge constraints.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

# Import models
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.models import (
    VehicleObservation,
    CameraGraphEdge,
    TimestampQuality,
    TimestampSource,
)

# Import comparison logic
from target_filter import _compare, _compare_color, _normalize_plate, MatchResult


@dataclass
class LinkResult:
    status: str  # possible, weak, conflicting, rejected, unknown
    temporal_feasibility: str  # valid, impossible, unknown
    attribute_comparisons: Dict[str, str]
    evidence_completeness: float
    link_score: float
    explanation: str


def evaluate_temporal_feasibility(
    obs_a: VehicleObservation, 
    obs_b: VehicleObservation, 
    edge: Optional[CameraGraphEdge]
) -> str:
    """
    Check if the transition between obs_a and obs_b is temporally feasible.

    Cross-camera time needs a comparable, trustworthy absolute-clock source.
    Local frame clocks and per-stream PTS remain valuable evidence, but cannot
    prove a travel-time bound between independent feeds.
    """
    if not obs_a.observed_at or not obs_b.observed_at:
        return "unknown"

    if obs_a.timestamp_quality != TimestampQuality.VALID or obs_b.timestamp_quality != TimestampQuality.VALID:
        return "unknown"

    if obs_a.timestamp_source != obs_b.timestamp_source:
        return "unknown"

    if obs_a.timestamp_source not in (TimestampSource.ABSOLUTE_TIMESTAMP, TimestampSource.SOURCE_PTS):
        return "unknown"
        
    if obs_a.timestamp_source == TimestampSource.SOURCE_PTS:
        if not getattr(obs_a, 'is_time_synchronized', False) or not getattr(obs_b, 'is_time_synchronized', False):
            return "unverified_cross_camera_time"
        
    if not edge:
        return "unknown"
        
    delta_seconds = (obs_b.observed_at - obs_a.observed_at).total_seconds()
    
    if delta_seconds < edge.min_travel_time or delta_seconds > edge.max_travel_time:
        return "impossible"
        
    return "valid"


def compare_observations(
    obs_a: VehicleObservation, 
    obs_b: VehicleObservation, 
    edge: Optional[CameraGraphEdge]
) -> LinkResult:
    """
    Compare two observations and return a LinkResult.
    """
    temp_feas = evaluate_temporal_feasibility(obs_a, obs_b, edge)
    
    if temp_feas == "impossible":
        return LinkResult(
            status="rejected",
            temporal_feasibility=temp_feas,
            attribute_comparisons={},
            evidence_completeness=1.0,
            link_score=0.0,
            explanation="Temporally impossible based on edge constraints."
        )
        
    # Compare attributes using strict target_filter semantics
    # Models use Enums or strings, so convert to string if Enum
    type_a = obs_a.vehicle_type.value if hasattr(obs_a.vehicle_type, 'value') else obs_a.vehicle_type
    type_b = obs_b.vehicle_type.value if hasattr(obs_b.vehicle_type, 'value') else obs_b.vehicle_type
    
    type_match = _compare(type_a, type_b)
    color_match = _compare_color(obs_a.color, obs_b.color)
    
    # Plate comparison
    plate_a = _normalize_plate(obs_a.plate)
    plate_b = _normalize_plate(obs_b.plate)
    plate_match = _compare(plate_a, plate_b)
    
    comparisons = {
        "type": type_match.value,
        "color": color_match.value,
        "plate": plate_match.value
    }
    
    # Determine Conflicts
    conflicts = []
    if type_match == MatchResult.NON_MATCH:
        conflicts.append("Type mismatch")
    if color_match == MatchResult.NON_MATCH:
        conflicts.append("Color mismatch")
    if plate_match == MatchResult.NON_MATCH:
        conflicts.append("Plate mismatch")
        
    # Completeness (how many attributes were actually compared vs UNKNOWN)
    possible_attrs = 3
    known_attrs = 0
    if type_match != MatchResult.UNKNOWN: known_attrs += 1
    if color_match != MatchResult.UNKNOWN: known_attrs += 1
    if plate_match != MatchResult.UNKNOWN: known_attrs += 1
    
    completeness = known_attrs / possible_attrs if possible_attrs > 0 else 0.0
    
    # Status and Score
    if conflicts:
        status = "conflicting"
        final_score = 0.0
        explanation = f"Conflicting attributes: {', '.join(conflicts)}"
    else:
        # No conflicts. Calculate positive evidence
        score = 0.0
        weight = 0.0
        if plate_match == MatchResult.MATCH:
            score += 0.6
            weight += 0.6
        if type_match == MatchResult.MATCH:
            score += 0.2
            weight += 0.2
        if color_match in (MatchResult.MATCH, MatchResult.APPROXIMATE):
            color_val = 0.2 if color_match == MatchResult.MATCH else 0.1
            score += color_val
            weight += 0.2
            
        final_score = score / weight if weight > 0 else 0.0
        
        if plate_match == MatchResult.MATCH:
            status = "possible"
        elif final_score >= 0.7:
            status = "possible"
        elif final_score >= 0.3:
            status = "weak"
        else:
            status = "unknown"
            
        if temp_feas == "unknown":
            # Strong attributes can still warrant a reviewer-visible candidate,
            # but never a route-strength claim without comparable timing.
            status = "weak" if final_score > 0 else "unknown"
            explanation = (
                f"Attribute-compatible candidate (score {final_score:.2f}); "
                "cross-camera timing is unavailable or unreliable."
            )
        else:
            explanation = f"Feasible transition. Match score: {final_score:.2f}."
        
    return LinkResult(
        status=status,
        temporal_feasibility=temp_feas,
        attribute_comparisons=comparisons,
        evidence_completeness=completeness,
        link_score=final_score,
        explanation=explanation
    )
