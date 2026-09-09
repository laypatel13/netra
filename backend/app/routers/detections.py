"""
Model 2 — detection ingestion and cross-camera route reconstruction.

This is the shared source of truth consumed by both the watchlist/alert path
(watchlist.py) and the GIS route-reconstruction path (this file's /route
endpoint). The ANPR pipeline (YOLO + OCR) should POST here for every plate it
reads, with a PTS-derived timestamp_ms — never wall-clock time. See
PLAN.md Section 8 for why.

Ordering note: cross-camera queries here (/plate, /route) order by
`created_at` (row-insert wall-clock time), not `timestamp_ms` (PTS).
timestamp_ms is PTS-derived per the sandbox protocol rules, but each
camera's PTS is relative to when *that specific RTSP connection* started —
it isn't a shared clock across cameras, and these are looping recordings
that reset at each loop point. Comparing raw PTS values between two
different cameras' detections isn't meaningful, so it can't be used to
answer "which camera did this plate pass first." created_at is a real
shared wall-clock timestamp and is what actually makes cross-camera
ordering correct; timestamp_ms is still stored and returned per-stop for
protocol compliance and any future within-camera analysis (dedup, velocity),
where PTS is exactly the right thing to use.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.routers.watchlist import check_plate_against_watchlist

router = APIRouter(prefix="/detections", tags=["detections"])


@router.post("", response_model=schemas.DetectionRead, summary="Record a new ANPR detection")
def record_detection(detection: schemas.DetectionCreate, db: Session = Depends(get_db)):
    """
    Called by the ANPR pipeline for every plate read off a frame.
    Also triggers a watchlist check — if matched, an alert should be raised
    by the caller (or wire this into a notification/websocket layer later).
    """
    camera = db.query(models.Camera).filter_by(camera_id=detection.camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="camera_id not registered — onboard it first")

    db_detection = models.Detection(
        plate_number=detection.plate_number,
        timestamp_ms=detection.timestamp_ms,
        camera_id=detection.camera_id,
        confidence=detection.confidence,
    )
    db.add(db_detection)
    db.commit()
    db.refresh(db_detection)

    # Fire-and-check watchlist match — real alert delivery (websocket/push) is a
    # Day 5 build item, this just confirms the match logic path exists end to end.
    check_plate_against_watchlist(detection.plate_number, db)

    return db_detection


@router.get("/plate/{plate_number}", response_model=List[schemas.DetectionRead], summary="Get all detections for a plate number")
def get_detections_for_plate(plate_number: str, db: Session = Depends(get_db)):
    return (
        db.query(models.Detection)
        .filter(models.Detection.plate_number == plate_number)
        .order_by(models.Detection.created_at.asc())
        .all()
    )


@router.get("/route/{plate_number}", response_model=schemas.VehicleRoute, summary="Reconstruct cross-camera route for a plate")
def reconstruct_route(plate_number: str, db: Session = Depends(get_db)):
    """
    The official test case: given a plate, return every camera it appeared
    on, in chronological order — e.g. Camera 1 -> Camera 13 -> Camera 15.
    This is what gets rendered as a route on the GIS map. Ordered by
    created_at, not timestamp_ms — see the module docstring for why.
    """
    detections = (
        db.query(models.Detection)
        .filter(models.Detection.plate_number == plate_number)
        .order_by(models.Detection.created_at.asc())
        .all()
    )
    if not detections:
        raise HTTPException(status_code=404, detail="no detections found for this plate")

    stops = [
        schemas.RouteStop(
            camera_id=d.camera_id,
            timestamp_ms=d.timestamp_ms,
            confidence=d.confidence,
        )
        for d in detections
    ]
    return schemas.VehicleRoute(plate_number=plate_number, stops=stops)
