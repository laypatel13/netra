"""
Model 1 — Centralised CCTV Registry & GIS Foundation.

Metadata/onboarding only — no video here. Camera IDs should be sourced from
the Sentinel /api/ingest catalogue (bulk import) or entered manually; never
invent camera IDs that don't correspond to something in the catalogue.
"""
import csv
import io
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import get_role, get_department, get_actor, require_admin
from app.routers.audit import log_action

router = APIRouter(prefix="/cameras", tags=["registry"])


@router.post("", response_model=schemas.CameraRead)
def onboard_camera(
    camera: schemas.CameraCreate,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
):
    """Manual or single-record onboarding. For bulk CSV/API onboarding, see /bulk and /bulk-csv."""
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
    log_action(db, actor, "camera.onboard", target_type="camera", target_id=camera.camera_id)
    return db_camera


@router.post("/bulk", response_model=List[schemas.CameraRead])
def onboard_cameras_bulk(
    cameras: List[schemas.CameraCreate],
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """Bulk onboarding from a JSON array — e.g. the parsed /api/ingest catalogue. Admin only."""
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
    log_action(db, actor, "camera.bulk_onboard", target_type="camera", details=f"onboarded {len(results)}")
    return results


@router.post("/bulk-csv", response_model=List[schemas.CameraRead])
async def onboard_cameras_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """
    Bulk onboarding from an uploaded CSV. Admin only.

    Expected columns (header row required):
      camera_id,name,latitude,longitude,department,camera_type,ownership,connectivity_status,storage_type,retention_days

    Only camera_id, latitude, longitude, department are required per row —
    the rest are optional and default sensibly. Rows with an already-
    registered camera_id are skipped, not failed.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    required = {"camera_id", "latitude", "longitude", "department"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=400,
            detail=f"CSV header must include at least: {sorted(required)}",
        )

    results = []
    skipped = 0
    for row in reader:
        if db.query(models.Camera).filter_by(camera_id=row["camera_id"]).first():
            skipped += 1
            continue
        try:
            point = from_shape(Point(float(row["longitude"]), float(row["latitude"])), srid=4326)
        except (ValueError, KeyError):
            skipped += 1
            continue
        db_camera = models.Camera(
            camera_id=row["camera_id"],
            name=row.get("name") or None,
            location=point,
            department=row["department"],
            camera_type=row.get("camera_type") or "ip",
            ownership=row.get("ownership") or None,
            connectivity_status=row.get("connectivity_status") or "unknown",
            storage_type=row.get("storage_type") or None,
            retention_days=int(row["retention_days"]) if row.get("retention_days") else None,
        )
        db.add(db_camera)
        results.append(db_camera)

    db.commit()
    for r in results:
        db.refresh(r)
    log_action(
        db, actor, "camera.bulk_csv_onboard", target_type="camera",
        details=f"onboarded {len(results)}, skipped {skipped}",
    )
    return results


@router.post("/sync-status")
def sync_camera_status(
    host: str,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """
    Pulls the /api/ingest catalogue from `host` and updates connectivity_status
    for already-onboarded cameras whose camera_id matches an entry. Does NOT
    onboard new cameras — this is a health-monitoring sync, not onboarding.
    Admin only.
    """
    try:
        resp = requests.get(f"http://{host}/api/ingest", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"could not reach catalogue at {host}: {e}")

    catalogue = data if isinstance(data, list) else data.get("cameras", [])
    updated = 0
    for entry in catalogue:
        cam_id = str(entry.get("id"))
        is_live = entry.get("live", entry.get("live_status", False))
        db_cam = db.query(models.Camera).filter_by(camera_id=cam_id).first()
        if db_cam:
            db_cam.connectivity_status = (
                models.ConnectivityStatus.online if is_live else models.ConnectivityStatus.offline
            )
            updated += 1
    db.commit()
    log_action(
        db, actor, "camera.status_sync", target_type="host", target_id=host,
        details=f"checked {len(catalogue)}, updated {updated}",
    )
    return {"host": host, "cameras_checked": len(catalogue), "cameras_updated": updated}


@router.get("", response_model=List[schemas.CameraRead])
def list_cameras(
    department: Optional[str] = None,
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
    caller_department: Optional[str] = Depends(get_department),
):
    """
    List registered cameras. A "department" role is scoped to its own
    department (X-Department header) regardless of the query param —
    admins/viewers can filter freely via the department query param.
    """
    query = db.query(models.Camera)
    if role == "department":
        if not caller_department:
            raise HTTPException(status_code=400, detail="X-Department header required for department role")
        query = query.filter(models.Camera.department == caller_department)
    elif department:
        query = query.filter(models.Camera.department == department)
    return query.all()


@router.get("/geojson")
def list_cameras_geojson(
    department: Optional[str] = None,
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
    caller_department: Optional[str] = Depends(get_department),
):
    """GeoJSON FeatureCollection for the Leaflet GIS map, with the same RBAC scoping as /cameras."""
    query = db.query(models.Camera)
    if role == "department":
        if not caller_department:
            raise HTTPException(status_code=400, detail="X-Department header required for department role")
        query = query.filter(models.Camera.department == caller_department)
    elif department:
        query = query.filter(models.Camera.department == department)

    features = []
    for cam in query.all():
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
    Minimal gap-analysis report: cameras grouped by department and
    connectivity status. Expand with real coverage-zone/geographic logic as
    the GIS layer matures — this is intentionally simple for Day 2.
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

