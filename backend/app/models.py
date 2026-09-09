"""
Core data model for netra, matching PLAN.md Section 6:

1. Camera        — Model 1 registry. Metadata only, no video. camera_id is
                    sourced from the Sentinel /api/ingest catalogue, never
                    self-generated.
2. Detection     — Model 2 output. Shared source of truth for both the
                    watchlist/alert path and the cross-camera tracking path.
                    `timestamp_ms` MUST be derived from stream PTS
                    (CAP_PROP_POS_MSEC or equivalent) — never wall-clock /
                    frame-arrival time. See PLAN.md Section 8.
3. WatchlistEntry — Representative watchlist DB. Real VAHAN/eGujCop/etc.
                    integration is explicitly out of scope for this build.
4. AuditLog      — basic audit trail for registry actions.
"""
import enum
import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Column, String, Float, DateTime, Enum, ForeignKey, Integer, Index
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
    """Matches the YOLO vehicle detector's labels (anpr/detector.py VEHICLE_CLASS_IDS)."""
    car = "car"
    motorcycle = "motorcycle"
    bus = "bus"
    truck = "truck"


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
    Model 2 — vehicle sighting event (ANPR + attribute tracking). Single
    source of truth for both the watchlist-match/alert path and the
    cross-camera route-reconstruction path.

    Every detected vehicle is recorded now, not just ones with a legible
    plate (PLAN.md Section 0b) — cctv.corp8.cloud's cameras are generic
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
    thumbnail_path = Column(String, nullable=True)  # saved crop — human-checkable fallback for vehicle_color
    timestamp_ms = Column(Float, nullable=False)  # derived from stream PTS — not wall-clock
    camera_id = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)  # used for cross-camera ordering — see detections.py

    camera = relationship("Camera", back_populates="detections")

    __table_args__ = (
        # Speeds up GET /detections/search, which filters on this exact pair.
        Index("ix_detections_vehicle_type_color", "vehicle_type", "vehicle_color"),
    )


class WatchlistEntry(Base):
    """
    Representative watchlist — not a real VAHAN/eGujCop/AFIS/NAFIS integration.

    An entry needs plate_number OR (vehicle_type AND vehicle_color), not
    neither — enforced at the API layer (schemas.py), not here. Attribute-
    based entries exist for exactly the "suspect vehicle, no known plate"
    case (PLAN.md Section 0b) — matching on them is a narrowing tool, not
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
    Day 2 — basic audit trail for registry actions. Supports the "enhanced
    cybersecurity/RBAC/auditability" bonus-consideration item in PLAN.md
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
