"""
Model 2 - detection ingestion and cross-camera tracking (plate + attributes).

This is the shared source of truth consumed by both the watchlist/alert path
(watchlist.py) and the GIS route-reconstruction path (this file's /route and
/search endpoints). The ANPR pipeline should POST here for every detected
vehicle - not just ones with a legible plate (PLAN.md Section 0b): most
cctv.corp8.cloud footage is generic wide-angle surveillance CCTV, not
purpose-built ANPR hardware, so a legible plate is the exception. Every
detection carries a PTS-derived timestamp_ms - never wall-clock time - per
PLAN.md Section 8.

Ordering note: cross-camera queries here (/plate, /route, /search) order by
`created_at` (row-insert wall-clock time), not `timestamp_ms` (PTS).
timestamp_ms is PTS-derived per the sandbox protocol rules, but each
camera's PTS is relative to when *that specific RTSP connection* started -
it isn't a shared clock across cameras, and these are looping recordings
that reset at each loop point. Comparing raw PTS values between two
different cameras' detections isn't meaningful, so it can't be used to
answer "which camera did this vehicle pass first." created_at is a real
shared wall-clock timestamp and is what actually makes cross-camera
ordering correct; timestamp_ms is still stored and returned per-stop for
protocol compliance and any future within-camera analysis (dedup, velocity),
where PTS is exactly the right thing to use.

Attribute-based tracking (vehicle_type + vehicle_color) is a narrowing
tool, not identification (PLAN.md Section 0b) - "red car" will match many
vehicles. It complements plate ANPR for exactly the case where a plate
isn't legible, the same way a real investigation narrows suspects by
vehicle description. /search results should be read as candidate
sightings, not a guaranteed single-vehicle route.
"""
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.serializers import detection_to_read
from app.routers.watchlist import check_detection_against_watchlist

router = APIRouter(prefix="/detections", tags=["detections"])

VEHICLE_TYPES = ("car", "motorcycle", "bus", "truck")

THUMBNAIL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "thumbnails"
)


@router.post("", response_model=schemas.DetectionRead, summary="Record a new vehicle detection")
async def record_detection(
    camera_id: str = Form(...),
    timestamp_ms: float = Form(...),
    confidence: float = Form(...),
    plate_number: Optional[str] = Form(None),
    vehicle_type: Optional[str] = Form(None),
    vehicle_color: Optional[str] = Form(None),
    thumbnail: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """
    Called by the ANPR pipeline for every detected vehicle, not just ones
    with a legible plate (PLAN.md Section 0b) - plate_number/vehicle_color
    may be omitted. Multipart (not JSON) so the crop thumbnail can be
    uploaded alongside the detection fields in one request.

    Also triggers a watchlist check (plate-exact or attribute-narrowed) -
    real push/websocket alert delivery isn't built, GET
    /watchlist/alerts/recent is the short-poll path the Dashboard uses.
    """
    if vehicle_type is not None and vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=422, detail=f"vehicle_type must be one of {VEHICLE_TYPES}")

    camera = db.query(models.Camera).filter_by(camera_id=camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="camera_id not registered - onboard it first")

    db_detection = models.Detection(
        plate_number=plate_number,
        vehicle_type=vehicle_type,
        vehicle_color=vehicle_color,
        timestamp_ms=timestamp_ms,
        camera_id=camera_id,
        confidence=confidence,
    )
    db.add(db_detection)
    db.commit()
    db.refresh(db_detection)

    if thumbnail is not None:
        os.makedirs(THUMBNAIL_DIR, exist_ok=True)
        thumb_path = os.path.join(THUMBNAIL_DIR, f"{db_detection.id}.jpg")
        with open(thumb_path, "wb") as f:
            f.write(await thumbnail.read())
        db_detection.thumbnail_path = thumb_path
        db.commit()
        db.refresh(db_detection)

    check_detection_against_watchlist(plate_number, vehicle_type, vehicle_color, db)

    return detection_to_read(db_detection)


@router.get("/{detection_id}/thumbnail", summary="Fetch a detection's saved crop image")
def get_thumbnail(detection_id: uuid.UUID, db: Session = Depends(get_db)):
    d = db.query(models.Detection).filter_by(id=detection_id).first()
    if not d or not d.thumbnail_path or not os.path.exists(d.thumbnail_path):
        raise HTTPException(status_code=404, detail="no thumbnail for this detection")
    return FileResponse(d.thumbnail_path, media_type="image/jpeg")


@router.get("/plate/{plate_number}", response_model=List[schemas.DetectionRead], summary="Get all detections for a plate number")
def get_detections_for_plate(plate_number: str, db: Session = Depends(get_db)):
    detections = (
        db.query(models.Detection)
        .filter(models.Detection.plate_number == plate_number)
        .order_by(models.Detection.created_at.asc())
        .all()
    )
    return [detection_to_read(d) for d in detections]


@router.get(
    "/search",
    response_model=schemas.VehicleRoute,
    summary="Find candidate vehicle sightings by type + color (no plate needed)",
)
def search_by_attributes(
    vehicle_type: str,
    vehicle_color: str,
    since: Optional[datetime] = Query(default=None, description="Only sightings at/after this time (ISO 8601)"),
    until: Optional[datetime] = Query(default=None, description="Only sightings at/before this time (ISO 8601)"),
    db: Session = Depends(get_db),
):
    """
    The plate-less counterpart to /route/{plate_number} (PLAN.md Section 0b)
    - for a suspect vehicle with no known plate. Results are candidate
    sightings ordered chronologically by created_at, same as /route; unlike
    a plate match, multiple different real vehicles may share the same
    type+color, so treat this as a narrowed list to cross-reference by time
    and route plausibility, not a confirmed single-vehicle path. since/until
    scope the search to a time window - without them this searches the
    entire history, which only gets noisier as real detection volume grows.
    """
    if vehicle_type not in VEHICLE_TYPES:
        raise HTTPException(status_code=422, detail=f"vehicle_type must be one of {VEHICLE_TYPES}")

    query = db.query(models.Detection).filter(
        models.Detection.vehicle_type == vehicle_type,
        models.Detection.vehicle_color == vehicle_color,
    )
    if since is not None:
        query = query.filter(models.Detection.created_at >= since)
    if until is not None:
        query = query.filter(models.Detection.created_at <= until)

    detections = query.order_by(models.Detection.created_at.asc()).all()
    if not detections:
        raise HTTPException(status_code=404, detail="no sightings found for this type/color combination (in this time window, if given)")

    stops = [_to_route_stop(d) for d in detections]
    return schemas.VehicleRoute(query=f"{vehicle_color} {vehicle_type}", stops=stops)


@router.get("/route/{plate_number}", response_model=schemas.VehicleRoute, summary="Reconstruct cross-camera route for a plate")
def reconstruct_route(plate_number: str, db: Session = Depends(get_db)):
    """
    The official test case: given a plate, return every camera it appeared
    on, in chronological order - e.g. Camera 1 -> Camera 13 -> Camera 15.
    This is what gets rendered as a route on the GIS map. Ordered by
    created_at, not timestamp_ms - see the module docstring for why.
    """
    detections = (
        db.query(models.Detection)
        .filter(models.Detection.plate_number == plate_number)
        .order_by(models.Detection.created_at.asc())
        .all()
    )
    if not detections:
        raise HTTPException(status_code=404, detail="no detections found for this plate")

    stops = [_to_route_stop(d) for d in detections]
    return schemas.VehicleRoute(plate_number=plate_number, stops=stops)


def _to_route_stop(d: models.Detection) -> schemas.RouteStop:
    return schemas.RouteStop(
        camera_id=d.camera_id,
        timestamp_ms=d.timestamp_ms,
        confidence=d.confidence,
        vehicle_type=d.vehicle_type.value if d.vehicle_type else None,
        vehicle_color=d.vehicle_color,
        thumbnail_url=f"/detections/{d.id}/thumbnail" if d.thumbnail_path else None,
    )
