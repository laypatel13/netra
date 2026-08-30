"""
Model 2 — detection ingestion and cross-camera route reconstruction.

This is the shared source of truth consumed by both the watchlist/alert path
(watchlist.py) and the GIS route-reconstruction path (this file's /route
endpoint). The ANPR pipeline (YOLO + OCR) should POST here for every plate it
reads, with a PTS-derived timestamp_ms — never wall-clock time. See
plan.md Section 8 for why.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.routers.watchlist import check_plate_against_watchlist

router = APIRouter(prefix="/detections", tags=["detections"])


@router.post("", response_model=schemas.DetectionRead)
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


@router.get("/plate/{plate_number}", response_model=List[schemas.DetectionRead])
def get_detections_for_plate(plate_number: str, db: Session = Depends(get_db)):
    return (
        db.query(models.Detection)
        .filter(models.Detection.plate_number == plate_number)
        .order_by(models.Detection.timestamp_ms.asc())
        .all()
    )


@router.get("/route/{plate_number}", response_model=schemas.VehicleRoute)
def reconstruct_route(plate_number: str, db: Session = Depends(get_db)):
    """
    The Day 6 tracking test case: given a plate, return every camera it
    appeared on, in chronological (PTS) order — e.g. Camera 1 -> Camera 13 ->
    Camera 15. This is what gets rendered as a route on the GIS map.
    """
    detections = (
        db.query(models.Detection)
        .filter(models.Detection.plate_number == plate_number)
        .order_by(models.Detection.timestamp_ms.asc())
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
