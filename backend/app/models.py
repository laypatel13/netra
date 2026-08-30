"""
Core data model for netra.

Three tables, matching plan.md Section 6:

1. Camera        — Model 1 registry. Metadata only, no video. camera_id is
                    sourced from the Sentinel /api/ingest catalogue, never
                    self-generated.
2. Detection     — Model 2 output. Shared source of truth for both the
                    watchlist/alert path and the cross-camera tracking path.
                    `timestamp_ms` MUST be derived from stream PTS
                    (CAP_PROP_POS_MSEC or equivalent) — never wall-clock /
                    frame-arrival time. See plan.md Section 8.
3. Watchlist     — Representative watchlist DB. Real VAHAN/eGujCop/etc.
                    integration is explicitly out of scope for this build.
"""
import enum
import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Column, String, Float, DateTime, Enum, ForeignKey, Integer
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


class Camera(Base):
    """Model 1 — registry & GIS. Metadata only, no video streaming here."""
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
    Model 2 — ANPR detection event. Single source of truth for both the
    watchlist-match/alert path and the cross-camera route-reconstruction path.
    """
    __tablename__ = "detections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_number = Column(String, nullable=False, index=True)
    timestamp_ms = Column(Float, nullable=False)  # derived from stream PTS — not wall-clock
    camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)  # row-insert time only, not for logic

    camera = relationship("Camera", back_populates="detections")


class WatchlistEntry(Base):
    """Representative watchlist — not a real VAHAN/eGujCop/AFIS/NAFIS integration."""
    __tablename__ = "watchlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_number = Column(String, unique=True, nullable=False, index=True)
    category = Column(Enum(WatchlistCategory), nullable=False)
    source = Column(String, default="representative-dataset")
    date_added = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """
    Day 2 — basic audit trail for registry actions. Supports the "enhanced
    cybersecurity/RBAC/auditability" bonus-consideration item in plan.md
    Section 11. `actor` comes from the (unauthenticated) X-Actor header —
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
