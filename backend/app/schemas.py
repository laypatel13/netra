"""Pydantic schemas - request/response shapes for the API layer."""
from datetime import datetime
from typing import Optional, List, Literal, Dict, Any
from uuid import UUID

from pydantic import BaseModel, model_validator

VehicleTypeLiteral = Literal["car", "motorcycle", "bus", "truck", "auto"]


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


class WatchlistUpdate(BaseModel):
    """
    Full replace of the identifying fields (PUT, not PATCH) - editing an
    entry always means retyping plate-or-attributes plus category, same
    shape and same validation as creating one.
    """
    plate_number: Optional[str] = None
    vehicle_type: Optional[VehicleTypeLiteral] = None
    vehicle_color: Optional[str] = None
    category: Literal["stolen", "suspect", "blacklisted"]

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


class ReviewRequest(BaseModel):
    """Reviewer decision on a single observation or cross-camera link."""
    action: Literal["accept", "reject", "reopen", "flag"]
    reviewer: str
    review_note: Optional[str] = None


class TargetStatusUpdate(BaseModel):
    status: Literal["active", "paused", "resolved"]


# ---- Investigation pipeline ingest (anpr/investigation_pipeline.py) ----

class EvidenceIngest(BaseModel):
    frame_index: int
    quality_score: float
    raw_path: str
    enhanced_path: Optional[str] = None
    ocr_candidate: Optional[str] = None
    ocr_confidence: Optional[float] = None
    vehicle_type: Optional[VehicleTypeLiteral] = None
    vehicle_color: Optional[str] = None
    timestamp: Optional[float] = None
    source_pts: Optional[float] = None
    evidence_state: Optional[Literal["match", "approximate", "non_match", "unknown", "conflicting"]] = None
    unknown_reason: Optional[
        Literal["not_visible", "not_detected", "extraction_failed", "low_confidence", "insufficient_frames", "unavailable"]
    ] = None
    extraction_method: Optional[str] = None
    is_simulated: bool = False


class CandidateIngest(BaseModel):
    camera_id: str
    track_id: int
    target_id: Optional[UUID] = None
    started_at: float
    ended_at: float
    final_score: float
    tier: str
    score_breakdown: Dict[str, Any]
    ocr_consensus: Dict[str, Any]
    evidence_completeness: Optional[float] = None
    evidence: List[EvidenceIngest]


class ObservationIngest(BaseModel):
    camera_id: str
    session_id: str
    track_id: int
    candidate_id: Optional[UUID] = None
    status: Literal["open", "updating", "finalized"]
    timestamp_source: Literal["source_pts", "frame_clock", "absolute_timestamp", "unknown"]
    is_simulated: bool = False
    observed_at: float  # unix seconds, UTC
    source_pts_start: Optional[float] = None
    source_pts_end: Optional[float] = None
    vehicle_type: Optional[VehicleTypeLiteral] = None
    color: Optional[str] = None
    color_confidence: Optional[float] = None
    plate: Optional[str] = None
    plate_confidence: Optional[float] = None
    evidence_completeness: Optional[float] = None
    overall_confidence: Optional[float] = None
    is_playback_repetition: bool = False
    is_time_synchronized: bool = False
    ingested_at: float = 0.0


class CameraHealthPayload(BaseModel):
    """Per-camera health snapshot sent inside the pipeline heartbeat."""
    camera_id: str
    status: str = "offline"  # online / offline / reconnecting
    connection_state: Optional[str] = None  # see anpr/camera_manager.py ConnectionState
    frames_read: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    vehicles_detected: int = 0
    tracks_active: int = 0
    matches: int = 0
    reconnect_count: int = 0
    last_frame_time: Optional[float] = None
    last_frame_received: float = 0.0
    current_error: Optional[str] = None
    worker_alive: bool = False


class HeartbeatPayload(BaseModel):
    pipeline_id: str = "main"
    cameras_configured: int = 0
    cameras_connected: int = 0
    cameras_active: int = 0
    vehicles_detected: int = 0
    tracks_created: int = 0
    active_targets: int = 0
    candidates_created: int = 0
    ocr_attempts: int = 0
    ocr_success: int = 0
    api_failures: int = 0
    per_camera: Optional[List[CameraHealthPayload]] = None
