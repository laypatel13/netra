"""
Core data model for netra, matching PLAN.md Section 6:

1. Camera        - Model 1 registry. Metadata only, no video. camera_id is
                    sourced from the Sentinel /api/ingest catalogue, never
                    self-generated.
2. Detection     - Model 2 output. Shared source of truth for both the
                    watchlist/alert path and the cross-camera tracking path.
                    `timestamp_ms` MUST be derived from stream PTS
                    (CAP_PROP_POS_MSEC or equivalent) - never wall-clock /
                    frame-arrival time. See PLAN.md Section 8.
3. WatchlistEntry - Representative watchlist DB. Real VAHAN/eGujCop/etc.
                    integration is explicitly out of scope for this build.
4. AuditLog      - basic audit trail for registry actions.
"""
import enum
import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Column, String, Float, DateTime, Enum, ForeignKey, Integer, Index, JSON, UniqueConstraint, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class CameraType(str, enum.Enum):
    analog = "analog"
    ip = "ip"


class StorageType(str, enum.Enum):
    cloud = "cloud"
    local = "local"


class ConnectivityStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"


class WatchlistCategory(str, enum.Enum):
    stolen = "stolen"
    suspect = "suspect"
    blacklisted = "blacklisted"


class VehicleType(str, enum.Enum):
    """Matches the YOLO vehicle detector's labels (anpr/detector.py VEHICLE_CLASS_IDS).

    "auto" (auto-rickshaw / three-wheeler) is not a COCO class - the detector
    reclassifies into it heuristically from a raw "truck" hit, since COCO's
    pretrained vehicle classes force auto-rickshaws into the closest of
    car/motorcycle/bus/truck otherwise. See detector.py's docstring.
    """
    car = "car"
    motorcycle = "motorcycle"
    bus = "bus"
    truck = "truck"
    auto = "auto"


class Camera(Base):
    """Model 1 - registry & GIS. Metadata only, no video streaming here."""
    __tablename__ = "cameras"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id = Column(String, unique=True, nullable=False, index=True)  # from /api/ingest
    name = Column(String, nullable=True)
    location = Column(Geography(geometry_type="POINT", srid=4326), nullable=True)
    department = Column(String, nullable=False)  # one of the 26 gov departments
    camera_type = Column(Enum(CameraType), nullable=False, default=CameraType.ip)
    ownership = Column(String, nullable=True)
    connectivity_status = Column(Enum(ConnectivityStatus), default=ConnectivityStatus.unknown)
    storage_type = Column(Enum(StorageType), nullable=True)
    retention_days = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    detections = relationship("Detection", back_populates="camera")


class Detection(Base):
    """
    Model 2 - vehicle sighting event (ANPR + attribute tracking). Single
    source of truth for both the watchlist-match/alert path and the
    cross-camera route-reconstruction path.

    Every detected vehicle is recorded now, not just ones with a legible
    plate (PLAN.md Section 0b) - cctv.corp8.cloud's cameras are generic
    wide-angle surveillance CCTV, not purpose-built ANPR hardware, so a
    legible plate is the exception rather than the rule. plate_number and
    vehicle_color may be null; vehicle_type and thumbnail_path are always
    populated (type comes free from the YOLO detector, thumbnail is the
    human-checkable fallback since computed color is unreliable under
    glare/artificial lighting).
    """
    __tablename__ = "detections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_number = Column(String, nullable=True, index=True)
    vehicle_type = Column(Enum(VehicleType), nullable=True)
    vehicle_color = Column(String, nullable=True)  # small named palette, see anpr/color.py
    thumbnail_path = Column(String, nullable=True)  # saved crop - human-checkable fallback for vehicle_color
    timestamp_ms = Column(Float, nullable=False)  # derived from stream PTS - not wall-clock
    camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)  # used for cross-camera ordering - see detections.py

    camera = relationship("Camera", back_populates="detections")

    __table_args__ = (
        # Speeds up GET /detections/search, which filters on this exact pair.
        Index("ix_detections_vehicle_type_color", "vehicle_type", "vehicle_color"),
    )


class WatchlistEntry(Base):
    """
    Representative watchlist - not a real VAHAN/eGujCop/AFIS/NAFIS integration.

    An entry needs plate_number OR (vehicle_type AND vehicle_color), not
    neither - enforced at the API layer (schemas.py), not here. Attribute-
    based entries exist for exactly the "suspect vehicle, no known plate"
    case (PLAN.md Section 0b) - matching on them is a narrowing tool, not
    unique identification, so treat matches on these as lower-confidence
    than an exact plate match (see watchlist.py).
    """
    __tablename__ = "watchlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_number = Column(String, unique=True, nullable=True, index=True)
    vehicle_type = Column(Enum(VehicleType), nullable=True)
    vehicle_color = Column(String, nullable=True)
    category = Column(Enum(WatchlistCategory), nullable=False)
    source = Column(String, default="representative-dataset")
    date_added = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """
    Day 2 - basic audit trail for registry actions. Supports the "enhanced
    cybersecurity/RBAC/auditability" bonus-consideration item in PLAN.md
    Section 11. `actor` comes from the (unauthenticated) X-Actor header -
    this is a demonstration of the trail existing, not real accountability
    until it sits behind real auth.
    """
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)  # e.g. "camera.onboard", "camera.status_sync"
    target_type = Column(String, nullable=True)
    target_id = Column(String, nullable=True)
    details = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TargetCategory(str, enum.Enum):
    stolen = "stolen"
    suspect = "suspect"
    blacklisted = "blacklisted"
    investigation = "investigation"


class TargetPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TargetStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    resolved = "resolved"


class TrackStatus(str, enum.Enum):
    collecting = "collecting"
    analyzing = "analyzing"
    completed = "completed"
    verified = "verified"
    rejected = "rejected"


class EvidenceState(str, enum.Enum):
    MATCH = "match"
    APPROXIMATE = "approximate"
    NON_MATCH = "non_match"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


class EvidenceUnknownReason(str, enum.Enum):
    NOT_VISIBLE = "not_visible"
    NOT_DETECTED = "not_detected"
    EXTRACTION_FAILED = "extraction_failed"
    LOW_CONFIDENCE = "low_confidence"
    INSUFFICIENT_FRAMES = "insufficient_frames"
    UNAVAILABLE = "unavailable"


class ObservationStatus(str, enum.Enum):
    OPEN = "open"
    UPDATING = "updating"
    FINALIZED = "finalized"


class RouteChainStatus(str, enum.Enum):
    ACTIVE = "active"
    EXTENDED = "extended"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    HISTORICAL = "historical"

class InvestigationStateStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    RESOLVED = "resolved"

class EvidenceReviewAction(str, enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REOPEN = "reopen"
    FLAG = "flag"

class EvidenceReviewEntityType(str, enum.Enum):
    VEHICLE_OBSERVATION = "vehicle_observation"
    CROSS_CAMERA_LINK = "cross_camera_link"
    ROUTE_CHAIN = "route_chain"


class TimestampSource(str, enum.Enum):
    SOURCE_PTS = "source_pts"
    FRAME_CLOCK = "frame_clock"
    ABSOLUTE_TIMESTAMP = "absolute_timestamp"
    UNKNOWN = "unknown"



class MachineAssessment(str, enum.Enum):
    """Mirrors anpr/linking.py LinkResult.status - keep the two in step."""
    POSSIBLE = "possible"
    WEAK = "weak"
    CONFLICTING = "conflicting"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

class HumanReviewState(str, enum.Enum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

class TimestampQuality(str, enum.Enum):
    VALID = "valid"
    UNRELIABLE = "unreliable"

class Mode(str, enum.Enum):
    REAL = "real"
    SIMULATED = "simulated"

class InvestigationTarget(Base):
    """
    A specific vehicle profile being hunted across the CCTV network.
    Separate from the general watchlist - these are focused multi-frame
    investigations.
    """
    __tablename__ = "investigation_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_number = Column(String, nullable=True, index=True)
    vehicle_type = Column(Enum(VehicleType), nullable=True)
    vehicle_color = Column(String, nullable=True)
    type_required = Column(Integer, default=0)   # 1 if strict match required
    color_required = Column(Integer, default=0)  # 1 if strict match required
    make = Column(String, default="unknown", nullable=True)
    description = Column(String, nullable=True)
    category = Column(Enum(TargetCategory), nullable=False, default=TargetCategory.investigation)
    priority = Column(Enum(TargetPriority), nullable=False, default=TargetPriority.medium)
    status = Column(Enum(TargetStatus), nullable=False, default=TargetStatus.active)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class VehicleTrack(Base):
    """
    The result of a multi-frame vehicle investigation on a single camera.
    """
    __tablename__ = "vehicle_tracks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    track_id = Column(Integer, nullable=False)  # camera-local
    target_id = Column(UUID(as_uuid=True), ForeignKey("investigation_targets.id"), nullable=True)
    
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    
    # Link to the standard detection event (single frame) if we still emit one
    best_detection_id = Column(UUID(as_uuid=True), ForeignKey("detections.id"), nullable=True)
    
    # Evidence fusion results
    final_score = Column(Float, nullable=True)
    tier = Column(String, nullable=True)
    score_breakdown = Column(JSON, nullable=True)
    ocr_consensus = Column(JSON, nullable=True)
    evidence_completeness = Column(Float, nullable=True)
    
    status = Column(Enum(TrackStatus), nullable=False, default=TrackStatus.completed)
    verified_by = Column(String, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    review_note = Column(String, nullable=True)


class TrackEvidence(Base):
    """
    Individual evidence frames selected for a vehicle track.
    """
    __tablename__ = "track_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_id = Column(UUID(as_uuid=True), ForeignKey("vehicle_tracks.id"), nullable=False)
    detection_id = Column(UUID(as_uuid=True), ForeignKey("detections.id"), nullable=True)
    
    frame_index = Column(Integer, nullable=False)
    timestamp = Column(Float, nullable=True)     # absolute time of frame
    source_pts = Column(Float, nullable=True)    # stream PTS
    
    quality_score = Column(Float, nullable=False)
    
    raw_path = Column(String, nullable=False)
    enhanced_path = Column(String, nullable=True)
    
    ocr_candidate = Column(String, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    
    vehicle_type = Column(Enum(VehicleType), nullable=True)
    vehicle_color = Column(String, nullable=True)
    
    # Evidence provenance
    evidence_state = Column(Enum(EvidenceState), nullable=True)
    unknown_reason = Column(Enum(EvidenceUnknownReason), nullable=True)
    extraction_method = Column(String, nullable=True)
    
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    created_at = Column(DateTime, default=datetime.utcnow)


class VehicleObservation(Base):
    """
    A finalized OCR/Detection from the live pipeline.
    """
    __tablename__ = "vehicle_observations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id = Column(String, index=True, nullable=False)
    session_id = Column(String, nullable=False, index=True)
    track_id = Column(Integer, nullable=False)  # local to camera/session
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("vehicle_tracks.id"), nullable=True)
    
    status = Column(Enum(ObservationStatus), nullable=False, default=ObservationStatus.OPEN)
    timestamp_source = Column(Enum(TimestampSource), nullable=False, default=TimestampSource.UNKNOWN)
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    
    observed_at = Column(DateTime, default=datetime.utcnow)
    source_pts_start = Column(Float, nullable=True)
    source_pts_end = Column(Float, nullable=True)
    
    vehicle_type = Column(Enum(VehicleType), nullable=True)
    color = Column(String, nullable=True)
    color_confidence = Column(Float, nullable=True)
    
    plate = Column(String, nullable=True)
    plate_confidence = Column(Float, nullable=True)
    
    direction = Column(String, nullable=True)
    speed = Column(Float, nullable=True)
    
    evidence_completeness = Column(Float, nullable=True)
    overall_confidence = Column(Float, nullable=True)
    
    verified_by = Column(String, nullable=True)
    human_review_state = Column(Enum(HumanReviewState), nullable=False, default=HumanReviewState.UNREVIEWED)
    timestamp_quality = Column(Enum(TimestampQuality), nullable=False, default=TimestampQuality.VALID)
    verified_at = Column(DateTime, nullable=True)
    review_note = Column(String, nullable=True)
    
    is_playback_repetition = Column(Boolean, nullable=False, default=False)
    is_time_synchronized = Column(Boolean, nullable=False, default=False)
    source_fingerprint = Column(String, unique=True, index=True, nullable=True)
    
    ingested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_obs_camera_time", "camera_id", "observed_at"),
        Index("ix_obs_simulated_time", "mode", "observed_at"),
    )


class CameraGraphEdge(Base):
    """
    Directed edge between two cameras representing a plausible vehicle path.
    """
    __tablename__ = "camera_graph_edges"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    destination_camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    
    direction = Column(String, nullable=True)  # e.g. "Northbound", "Exit to Main St"
    
    # Temporal feasibility (in seconds)
    min_travel_time = Column(Float, nullable=False)
    max_travel_time = Column(Float, nullable=False)
    
    distance_meters = Column(Float, nullable=True)
    
    provenance = Column(String, nullable=False, default="configured") # configured, inferred, simulated
    confidence = Column(Float, nullable=False, default=1.0)
    enabled = Column(Integer, default=1)
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    graph_version = Column(Integer, nullable=False, default=1)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CrossCameraLinkCandidate(Base):
    """
    A proposed match between two VehicleObservations across different cameras.
    """
    __tablename__ = "cross_camera_link_candidates"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    source_observation_id = Column(UUID(as_uuid=True), ForeignKey("vehicle_observations.id"), nullable=False)
    destination_observation_id = Column(UUID(as_uuid=True), ForeignKey("vehicle_observations.id"), nullable=False)
    
    camera_edge_id = Column(UUID(as_uuid=True), ForeignKey("camera_graph_edges.id"), nullable=True)
    
    temporal_feasibility = Column(String, nullable=False)  # valid, impossible, unknown
    
    attribute_comparisons = Column(JSON, nullable=False)
    
    evidence_completeness = Column(Float, nullable=False)
    link_score = Column(Float, nullable=False)
    
    explanation = Column(String, nullable=True)
    provenance = Column(String, nullable=True)
    
    machine_assessment = Column(Enum(MachineAssessment), nullable=False, default=MachineAssessment.UNKNOWN)
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    
    verified_by = Column(String, nullable=True)
    human_review_state = Column(Enum(HumanReviewState), nullable=False, default=HumanReviewState.UNREVIEWED)
    timestamp_quality = Column(Enum(TimestampQuality), nullable=False, default=TimestampQuality.VALID)
    verified_at = Column(DateTime, nullable=True)
    review_note = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_link_source", "source_observation_id"),
        Index("ix_link_dest", "destination_observation_id"),
        Index("ix_link_edge", "camera_edge_id"),
        Index("ix_link_assessment", "machine_assessment"),
    )


class RouteChain(Base):
    """
    A sequence of observations and links representing a potential path.
    """
    __tablename__ = "route_chains"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("investigation_targets.id"), nullable=False)
    
    status = Column(Enum(RouteChainStatus), nullable=False, default=RouteChainStatus.ACTIVE)
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    
    evidence_completeness = Column(Float, nullable=True)
    explanation = Column(String, nullable=True)
    provenance = Column(String, nullable=True)
    
    route_fingerprint = Column(String, nullable=False, unique=True, index=True)
    graph_version = Column(Integer, nullable=False, default=1)
    is_superseded = Column(Integer, nullable=False, default=0)
    
    verified_by = Column(String, nullable=True)
    human_review_state = Column(Enum(HumanReviewState), nullable=False, default=HumanReviewState.UNREVIEWED)
    timestamp_quality = Column(Enum(TimestampQuality), nullable=False, default=TimestampQuality.VALID)
    verified_at = Column(DateTime, nullable=True)
    review_note = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class RouteChainObservation(Base):
    """
    Association table linking a RouteChain to a VehicleObservation with sequence ordering.
    """
    __tablename__ = "route_chain_observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_chain_id = Column(UUID(as_uuid=True), ForeignKey("route_chains.id"), nullable=False)
    observation_id = Column(UUID(as_uuid=True), ForeignKey("vehicle_observations.id"), nullable=False)
    
    sequence_order = Column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_rc_obs_chain_seq", "route_chain_id", "sequence_order"),
        Index("ix_rc_obs_observation", "observation_id"),
    )


class RouteChainLink(Base):
    """
    Association table linking a RouteChain to a CrossCameraLinkCandidate.
    """
    __tablename__ = "route_chain_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_chain_id = Column(UUID(as_uuid=True), ForeignKey("route_chains.id"), nullable=False)
    link_candidate_id = Column(UUID(as_uuid=True), ForeignKey("cross_camera_link_candidates.id"), nullable=False)
    
    sequence_order = Column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_rc_link_chain_seq", "route_chain_id", "sequence_order"),
        Index("ix_rc_link_candidate", "link_candidate_id"),
    )

class InvestigationState(Base):
    """
    Durable investigation-level state object representing the current computed state.
    """
    __tablename__ = "investigation_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("investigation_targets.id"), nullable=False, unique=True, index=True)
    
    status = Column(Enum(InvestigationStateStatus), nullable=False, default=InvestigationStateStatus.ACTIVE)
    last_processed_at = Column(DateTime, nullable=True, index=True)
    
    last_observation_at = Column(DateTime, nullable=True)
    last_seen_observation_id = Column(UUID(as_uuid=True), ForeignKey("vehicle_observations.id"), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    last_seen_camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=True)
    
    state_version = Column(Integer, nullable=False, default=1)
    graph_version = Column(Integer, nullable=False, default=1)
    search_truncated = Column(Integer, nullable=False, default=0)
    
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class EvidenceReview(Base):
    """
    Append-only review model for human verification events.
    Does NOT own idempotency - that belongs to InvestigationEvent.
    """
    __tablename__ = "evidence_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(Enum(EvidenceReviewEntityType), nullable=False)
    entity_id = Column(String, nullable=False)
    action = Column(Enum(EvidenceReviewAction), nullable=False)
    
    reviewer = Column(String, nullable=False)
    review_note = Column(String, nullable=True)
    
    previous_state = Column(String, nullable=True)
    new_state = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_evidence_review_entity", "entity_type", "entity_id", "created_at"),
    )


class GraphVersion(Base):
    """
    Global graph version counter.
    Bumped whenever a CameraGraphEdge is created, modified, or disabled.
    Historical transitions retain the graph_version under which they were generated.
    """
    __tablename__ = "graph_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(Integer, nullable=False, index=True)
    reason = Column(String, nullable=False)  # e.g. "edge_created", "edge_disabled", "edge_modified"
    affected_edge_id = Column(UUID(as_uuid=True), ForeignKey("camera_graph_edges.id"), nullable=True)
    mode = Column(Enum(Mode), nullable=False, default=Mode.REAL)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('version', 'mode', name='ix_graph_versions_version_mode'),
    )


class PipelineHeartbeat(Base):
    """
    Tracks whether the ANPR pipeline process is actually running.
    The pipeline sends periodic heartbeats; the backend checks recency
    to determine if the scanner is truly online vs just having an
    active target status.

    Extended for multi-camera investigation mode: tracks per-camera
    health alongside global aggregates.
    """
    __tablename__ = "pipeline_heartbeats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_id = Column(String, nullable=False, unique=True, index=True)  # e.g. 'main' or hostname
    cameras_configured = Column(Integer, default=0)
    cameras_connected = Column(Integer, default=0)
    cameras_active = Column(Integer, default=0)
    vehicles_detected = Column(Integer, default=0)
    tracks_created = Column(Integer, default=0)
    active_targets = Column(Integer, default=0)
    candidates_created = Column(Integer, default=0)
    ocr_attempts = Column(Integer, default=0)
    ocr_success = Column(Integer, default=0)
    api_failures = Column(Integer, default=0)
    last_heartbeat = Column(DateTime, nullable=False, default=datetime.utcnow)
    per_camera_health = Column(JSON, nullable=True)  # list of per-camera health dicts


class InvestigationEventType(str, enum.Enum):
    NEW_OBSERVATION = "new_observation"
    VERIFICATION = "verification"


class EventProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class InvestigationEvent(Base):
    """
    Idempotent event wrapper for the investigation state engine.
    """
    __tablename__ = "investigation_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(Enum(InvestigationEventType), nullable=False)
    event_id = Column(String, unique=True, index=True, nullable=False)
    entity_id = Column(String, nullable=False)
    attempt_count = Column(Integer, default=0)
    last_error = Column(String, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    payload = Column(JSON, nullable=True)
    status = Column(Enum(EventProcessingStatus), default=EventProcessingStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_inv_event_type_entity", "event_type", "entity_id", "status"),
    )
