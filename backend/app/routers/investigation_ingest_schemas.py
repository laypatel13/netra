"""
Schemas for ANPR pipeline ingestion.
"""
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel


class EvidenceIngest(BaseModel):
    frame_index: int
    quality_score: float
    raw_path: str
    enhanced_path: Optional[str]
    ocr_candidate: Optional[str]
    ocr_confidence: Optional[float]
    vehicle_type: Optional[str]
    vehicle_color: Optional[str]
    timestamp: Optional[float] = None
    source_pts: Optional[float] = None
    evidence_state: Optional[str] = None
    unknown_reason: Optional[str] = None
    extraction_method: Optional[str] = None
    is_simulated: bool = False

class RecordingSessionIngest(BaseModel):
    session_id: str
    camera_id: str
    source_date_time: Optional[float] = None
    timestamp_quality: str = "unreliable"
    provenance_type: str = "UNKNOWN"
    source_identifier: Optional[str] = None
    synchronization_status: str = "unknown"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    graph_version: int = 1
    mode: str = "REAL"

class ObservationIngest(BaseModel):
    camera_id: str
    session_id: Optional[str] = None
    track_id: int
    candidate_id: Optional[UUID] = None
    status: str
    timestamp_source: str
    is_simulated: bool = False
    
    observed_at: float
    source_pts_start: Optional[float] = None
    source_pts_end: Optional[float] = None
    
    vehicle_type: Optional[str] = None
    color: Optional[str] = None
    color_confidence: Optional[float] = None
    
    plate: Optional[str] = None
    plate_confidence: Optional[float] = None
    
    direction: Optional[str] = None
    speed: Optional[float] = None
    
    evidence_completeness: Optional[float] = None
    overall_confidence: Optional[float] = None
    is_playback_repetition: bool = False
    is_time_synchronized: bool = False
    ingested_at: float = 0.0


class CandidateIngest(BaseModel):
    camera_id: str
    track_id: int
    target_id: Optional[UUID]
    started_at: float
    ended_at: float
    final_score: float
    tier: str
    score_breakdown: Dict[str, Any]
    ocr_consensus: Dict[str, Any]
    evidence_completeness: Optional[float] = None
    evidence: List[EvidenceIngest]


class CameraHealthPayload(BaseModel):
    """Per-camera health snapshot sent inside the pipeline heartbeat."""
    camera_id: str
    status: str = "offline"  # online / offline / reconnecting
    connection_state: Optional[str] = None  # Phase 1: disconnected/connecting/connected/degraded/reconnecting/stopped/error
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
    stats: Optional[Dict[str, Any]] = None
    per_camera: Optional[List[CameraHealthPayload]] = None
