"""Pydantic schemas - request/response shapes for the API layer."""
from datetime import datetime
from typing import Optional, List, Literal, Dict, Any
from uuid import UUID

from pydantic import BaseModel, model_validator

VehicleTypeLiteral = Literal["car", "motorcycle", "bus", "truck"]


# ---- Camera (Model 1 registry) ----

class CameraCreate(BaseModel):
    camera_id: str
    name: Optional[str] = None
    latitude: float
    longitude: float
    department: str
    camera_type: Literal["analog", "ip"]
    ownership: Optional[str] = None
    connectivity_status: Optional[Literal["online", "offline", "unknown"]] = "unknown"
    storage_type: Optional[Literal["cloud", "local"]] = None
    retention_days: Optional[int] = None


class CameraRead(BaseModel):
    id: UUID
    camera_id: str
    name: Optional[str]
    department: str
    camera_type: str
    connectivity_status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Detection (Model 2 - shared by watchlist and tracking) ----
#
# plate_number/vehicle_color are optional - most cctv.corp8.cloud footage
# doesn't yield a legible plate (PLAN.md Section 0b), so every vehicle
# sighting is recorded with whatever subset of identifying info is
# available. vehicle_type and a thumbnail are expected on every detection
# (type comes free from the vehicle detector; the thumbnail is the
# human-checkable fallback for when computed color is wrong).

class DetectionCreate(BaseModel):
    plate_number: Optional[str] = None
    vehicle_type: Optional[VehicleTypeLiteral] = None
    vehicle_color: Optional[str] = None
    timestamp_ms: float  # must be PTS-derived, see models.py docstring
    camera_id: str
    confidence: float


class DetectionRead(BaseModel):
    id: UUID
    plate_number: Optional[str]
    vehicle_type: Optional[str]
    vehicle_color: Optional[str]
    thumbnail_url: Optional[str] = None
    timestamp_ms: float
    camera_id: str
    confidence: float

    class Config:
        from_attributes = True


class RouteStop(BaseModel):
    camera_id: str
    timestamp_ms: float
    confidence: float
    vehicle_type: Optional[str] = None
    vehicle_color: Optional[str] = None
    thumbnail_url: Optional[str] = None


class VehicleRoute(BaseModel):
    plate_number: Optional[str] = None
    query: Optional[str] = None  # describes an attribute-based query, e.g. "car / red"
    stops: List[RouteStop]  # chronologically ordered by created_at - see detections.py


# ---- Watchlist ----
#
# An entry needs plate_number OR (vehicle_type AND vehicle_color) - not
# neither. Attribute-based entries are for the "suspect vehicle, no known
# plate" case (PLAN.md Section 0b); matches against them are a narrowing
# tool, not unique identification.

class WatchlistCreate(BaseModel):
    plate_number: Optional[str] = None
    vehicle_type: Optional[VehicleTypeLiteral] = None
    vehicle_color: Optional[str] = None
    category: Literal["stolen", "suspect", "blacklisted"]
    source: Optional[str] = "representative-dataset"

    @model_validator(mode="after")
    def _require_plate_or_attributes(self):
        has_plate = bool(self.plate_number)
        has_attributes = bool(self.vehicle_type) and bool(self.vehicle_color)
        if not has_plate and not has_attributes:
            raise ValueError("watchlist entry needs plate_number OR both vehicle_type and vehicle_color")
        return self


class WatchlistRead(BaseModel):
    id: UUID
    plate_number: Optional[str]
    vehicle_type: Optional[str]
    vehicle_color: Optional[str]
    category: str
    source: str
    date_added: datetime

    class Config:
        from_attributes = True


class WatchlistMatch(BaseModel):
    matched: bool
    tier: Optional[Literal["exact_plate", "attributes"]] = None
    entry: Optional[WatchlistRead] = None


class WatchlistAlert(BaseModel):
    """One row in the GET /watchlist/alerts/recent feed."""
    detection: DetectionRead
    tier: Literal["exact_plate", "attributes"]
    watchlist_entry: WatchlistRead


# ---- Audit log ----

class AuditLogRead(BaseModel):
    id: UUID
    actor: str
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    details: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Investigation Targets ----

class InvestigationTargetCreate(BaseModel):
    plate_number: Optional[str] = None
    vehicle_type: Optional[VehicleTypeLiteral] = None
    vehicle_color: Optional[str] = None
    type_required: bool = False
    color_required: bool = False
    make: Optional[str] = "unknown"
    description: Optional[str] = None
    category: Literal["stolen", "suspect", "blacklisted", "investigation"] = "investigation"
    priority: Literal["low", "medium", "high", "critical"] = "medium"

    @model_validator(mode="after")
    def _require_some_identifying_info(self):
        has_plate = bool(self.plate_number)
        has_type = bool(self.vehicle_type)
        has_color = bool(self.vehicle_color)
        if not has_plate and not has_type and not has_color:
            raise ValueError("Target needs at least one of: plate_number, vehicle_type, vehicle_color")
        return self


class InvestigationTargetRead(BaseModel):
    id: UUID
    plate_number: Optional[str]
    vehicle_type: Optional[str]
    vehicle_color: Optional[str]
    type_required: bool
    color_required: bool
    make: Optional[str]
    description: Optional[str]
    category: str
    priority: str
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class EvidenceRead(BaseModel):
    id: UUID
    frame_index: int
    quality_score: float
    raw_path: str
    enhanced_path: Optional[str]
    ocr_candidate: Optional[str]
    ocr_confidence: Optional[float]
    vehicle_type: Optional[str]
    vehicle_color: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class CandidateRead(BaseModel):
    id: UUID
    camera_id: str
    track_id: int
    target_id: Optional[UUID]
    started_at: datetime
    ended_at: Optional[datetime]
    final_score: Optional[float]
    tier: Optional[str]
    score_breakdown: Optional[Dict[str, Any]]
    ocr_consensus: Optional[Dict[str, Any]]
    evidence_completeness: Optional[float] = None
    status: str
    verified_by: Optional[str]
    verified_at: Optional[datetime]
    review_note: Optional[str] = None
    
    # Nested evidence available when requesting detailed candidate
    evidence: Optional[List[EvidenceRead]] = None

    class Config:
        from_attributes = True


class VerificationAction(BaseModel):
    action: Literal["verify", "reject"]
    verifier: str
    review_note: Optional[str] = None
