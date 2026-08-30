"""
Model 1 — Centralised CCTV Registry & GIS Foundation.

Metadata/onboarding only — no video here. Camera IDs should be sourced from
the Sentinel /api/ingest catalogue (bulk import) or entered manually; never
invent camera IDs that don't correspond to something in the catalogue.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/cameras", tags=["registry"])


@router.post("", response_model=schemas.CameraRead)
def onboard_camera(camera: schemas.CameraCreate, db: Session = Depends(get_db)):
    """Manual or single-record onboarding. For bulk CSV/API onboarding, call this in a loop."""
    existing = db.query(models.Camera).filter_by(camera_id=camera.camera_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="camera_id already registered")

    point = from_shape(Point(camera.longitude, camera.latitude), srid=4326)
    db_camera = models.Camera(
        camera_id=camera.camera_id,
        name=camera.name,
        location=point,
        department=camera.department,
        camera_type=camera.camera_type,
        ownership=camera.ownership,
        connectivity_status=camera.connectivity_status,
        storage_type=camera.storage_type,
        retention_days=camera.retention_days,
    )
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera


@router.post("/bulk", response_model=List[schemas.CameraRead])
def onboard_cameras_bulk(cameras: List[schemas.CameraCreate], db: Session = Depends(get_db)):
    """Bulk onboarding — feed this the parsed rows of a CSV or the /api/ingest catalogue."""
    results = []
    for cam in cameras:
        if db.query(models.Camera).filter_by(camera_id=cam.camera_id).first():
            continue  # skip already-onboarded cameras rather than failing the whole batch
        point = from_shape(Point(cam.longitude, cam.latitude), srid=4326)
        db_camera = models.Camera(
            camera_id=cam.camera_id,
            name=cam.name,
            location=point,
            department=cam.department,
            camera_type=cam.camera_type,
            ownership=cam.ownership,
            connectivity_status=cam.connectivity_status,
            storage_type=cam.storage_type,
            retention_days=cam.retention_days,
        )
        db.add(db_camera)
        results.append(db_camera)
    db.commit()
    for r in results:
        db.refresh(r)
    return results


@router.get("", response_model=List[schemas.CameraRead])
def list_cameras(department: str | None = None, db: Session = Depends(get_db)):
    """List registered cameras, optionally filtered by department (GIS map layer filter)."""
    query = db.query(models.Camera)
    if department:
        query = query.filter(models.Camera.department == department)
    return query.all()


@router.get("/geojson")
def list_cameras_geojson(db: Session = Depends(get_db)):
    """GeoJSON FeatureCollection for the Leaflet GIS map."""
    cameras = db.query(models.Camera).all()
    features = []
    for cam in cameras:
        shape = to_shape(cam.location) if cam.location is not None else None
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [shape.x, shape.y] if shape else [0, 0],
            },
            "properties": {
                "camera_id": cam.camera_id,
                "name": cam.name,
                "department": cam.department,
                "camera_type": cam.camera_type,
                "connectivity_status": cam.connectivity_status,
            },
        })
    return {"type": "FeatureCollection", "features": features}


@router.get("/gap-analysis")
def gap_analysis(db: Session = Depends(get_db)):
    """
    Minimal gap-analysis report: cameras with unknown/offline connectivity
    status, grouped by department. Expand with real coverage-zone logic as
    the GIS layer matures.
    """
    cameras = db.query(models.Camera).all()
    report: dict[str, dict[str, int]] = {}
    for cam in cameras:
        dept_report = report.setdefault(cam.department, {"online": 0, "offline": 0, "unknown": 0})
        dept_report[cam.connectivity_status] = dept_report.get(cam.connectivity_status, 0) + 1
    return report


@router.get("/{camera_id}", response_model=schemas.CameraRead)
def get_camera(camera_id: str, db: Session = Depends(get_db)):
    camera = db.query(models.Camera).filter_by(camera_id=camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="camera not found")
    return camera
