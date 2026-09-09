"""Pydantic schemas — request/response shapes for the API layer."""
from datetime import datetime
from typing import Optional, List, Literal
from uuid import UUID

from pydantic import BaseModel


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


# ---- Detection (Model 2 — shared by watchlist and tracking) ----

class DetectionCreate(BaseModel):
    plate_number: str
    timestamp_ms: float  # must be PTS-derived, see models.py docstring
    camera_id: str
    confidence: float


class DetectionRead(BaseModel):
    id: UUID
    plate_number: str
    timestamp_ms: float
    camera_id: str
    confidence: float

    class Config:
        from_attributes = True


class RouteStop(BaseModel):
    camera_id: str
    timestamp_ms: float
    confidence: float


class VehicleRoute(BaseModel):
    plate_number: str
    stops: List[RouteStop]  # chronologically ordered by timestamp_ms


# ---- Watchlist ----

class WatchlistCreate(BaseModel):
    plate_number: str
    category: Literal["stolen", "suspect", "blacklisted"]
    source: Optional[str] = "representative-dataset"


class WatchlistRead(BaseModel):
    id: UUID
    plate_number: str
    category: str
    source: str
    date_added: datetime

    class Config:
        from_attributes = True


class WatchlistMatch(BaseModel):
    matched: bool
    entry: Optional[WatchlistRead] = None


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
