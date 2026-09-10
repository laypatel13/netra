"""
Model 1 - Centralised CCTV Registry & GIS Foundation.

Metadata/onboarding only - no video here. Camera IDs should be sourced from
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


@router.post("", response_model=schemas.CameraRead, summary="Onboard a single camera")
def onboard_camera(
    camera: schemas.CameraCreate,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """Manual or single-record onboarding. For bulk CSV/API onboarding, see /bulk and /bulk-csv. Admin only - was missing this check, unlike every other mutating endpoint here."""
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


@router.post("/bulk", response_model=List[schemas.CameraRead], summary="Bulk onboard cameras from JSON array")
def onboard_cameras_bulk(
    cameras: List[schemas.CameraCreate],
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """Bulk onboarding from a JSON array - e.g. the parsed /api/ingest catalogue. Admin only."""
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


@router.post("/bulk-csv", response_model=List[schemas.CameraRead], summary="Bulk onboard cameras from CSV upload")
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

    Only camera_id, latitude, longitude, department are required per row -
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

        camera_type = row.get("camera_type") or "ip"
        connectivity_status = row.get("connectivity_status") or "unknown"
        storage_type = row.get("storage_type") or None
        if (
            camera_type not in ("analog", "ip")
            or connectivity_status not in ("online", "offline", "unknown")
            or (storage_type is not None and storage_type not in ("cloud", "local"))
        ):
            # Bad enum value would otherwise 500 at the DB layer mid-batch -
            # skip the row instead of failing the whole import.
            skipped += 1
            continue

        db_camera = models.Camera(
            camera_id=row["camera_id"],
            name=row.get("name") or None,
            location=point,
            department=row["department"],
            camera_type=camera_type,
            ownership=row.get("ownership") or None,
            connectivity_status=connectivity_status,
            storage_type=storage_type,
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


@router.post("/sync-status", summary="Sync camera health status from upstream catalogue")
def sync_camera_status(
    host: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """
    Pulls the camera catalogue from cctv.corp8.cloud/cameras.json (authenticated)
    and updates connectivity_status for already-onboarded cameras whose camera_id
    matches an entry. Does NOT onboard new cameras - health-monitoring sync only.
    Admin only.
    """
    import os
    from app.routers.feeds import _get_cctv_session

    h = host or os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()
    cctv_host = os.getenv("CCTV_HOST", "cctv.corp8.cloud").strip()

    try:
        if h == cctv_host:
            # New authenticated endpoint
            session = _get_cctv_session()
            resp = session.get(f"https://{h}/cameras.json", timeout=15)
        else:
            resp = requests.get(f"http://{h}/api/ingest", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"could not reach catalogue at {h}: {e}")

    catalogue = data if isinstance(data, list) else data.get("cameras", [])
    updated = 0
    skipped_no_field = 0
    for entry in catalogue:
        cam_id = str(entry.get("id", entry.get("camera_id", "")))
        # `live`/`live_status` isn't always present in the catalogue response
        # (observed missing entirely from cctv.corp8.cloud's /cameras.json).
        # A missing field means "the upstream API didn't tell us" - treating
        # that the same as "confirmed offline" would silently overwrite real
        # status with a false negative, so leave the camera's status alone
        # when the field simply isn't there.
        if "live" not in entry and "live_status" not in entry:
            skipped_no_field += 1
            continue
        is_live = entry.get("live", entry.get("live_status"))
        db_cam = db.query(models.Camera).filter_by(camera_id=cam_id).first()
        if db_cam:
            db_cam.connectivity_status = (
                models.ConnectivityStatus.online if is_live else models.ConnectivityStatus.offline
            )
            updated += 1
    db.commit()
    log_action(
        db, actor, "camera.status_sync", target_type="host", target_id=h,
        details=f"checked {len(catalogue)}, updated {updated}, skipped {skipped_no_field} (no live field)",
    )
    return {
        "host": h,
        "cameras_checked": len(catalogue),
        "cameras_updated": updated,
        "cameras_skipped_no_live_field": skipped_no_field,
    }


@router.get("", response_model=List[schemas.CameraRead], summary="List registered cameras with RBAC scoping")
def list_cameras(
    department: Optional[str] = None,
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
    caller_department: Optional[str] = Depends(get_department),
):
    """
    List registered cameras. A "department" role is scoped to its own
    department (X-Department header) regardless of the query param -
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


@router.get("/geojson", summary="GeoJSON FeatureCollection for the GIS map")
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


@router.get(
    "/gap-analysis",
    summary="Coverage gap-analysis report",
    response_description="Structured coverage report with per-department stats, stale cameras, missing departments, and geographic quadrant analysis.",
)
def gap_analysis(db: Session = Depends(get_db)):
    """
    Day 3 - rich gap-analysis report generator.

    Returns:
      - per_department: breakdown of cameras by department with online/offline/unknown
        counts, total, and coverage percentage (online / total * 100).
      - stale_cameras: cameras still in 'unknown' status (never synced).
      - missing_departments: known Gujarat government departments with zero onboarded cameras.
      - geographic_coverage: cameras per lat/lng quadrant (1-degree grid) to identify
        geographically uncovered zones.
      - summary: aggregate totals across all departments.
    """
    # 26 known Gujarat government departments per the hackathon brief
    KNOWN_DEPARTMENTS = [
        "Gujarat Police", "Home Department", "Roads & Buildings", "Urban Development",
        "Revenue Department", "Education Department", "Health & Family Welfare",
        "Narmada Water Resources", "Energy & Petrochemicals", "Industries & Mines",
        "Agriculture & Co-operation", "Panchayat Rural Housing", "Ports & Transport",
        "Forest & Environment", "Labour & Employment", "Social Justice & Empowerment",
        "Science & Technology", "Information & Broadcasting", "General Administration",
        "Finance Department", "Legal Department", "Legislative & Parliamentary Affairs",
        "Food Civil Supplies", "Women & Child Development", "Tribal Development",
        "Sports Youth & Cultural Activities",
    ]

    cameras = db.query(models.Camera).all()

    # --- Per-department breakdown ---
    dept_stats: dict[str, dict] = {}
    for cam in cameras:
        d = dept_stats.setdefault(cam.department, {"online": 0, "offline": 0, "unknown": 0, "total": 0})
        status_key = cam.connectivity_status if cam.connectivity_status in ("online", "offline", "unknown") else "unknown"
        d[status_key] += 1
        d["total"] += 1

    for dept in dept_stats.values():
        dept["coverage_pct"] = round((dept["online"] / dept["total"]) * 100, 1) if dept["total"] > 0 else 0.0

    # --- Stale cameras (never synced - still 'unknown') ---
    stale = []
    for cam in cameras:
        if cam.connectivity_status == "unknown" or cam.connectivity_status == models.ConnectivityStatus.unknown:
            stale.append({"camera_id": cam.camera_id, "department": cam.department, "name": cam.name})

    # --- Missing departments (known list minus what's onboarded) ---
    onboarded_depts = set(dept_stats.keys())
    missing = [d for d in KNOWN_DEPARTMENTS if d not in onboarded_depts]

    # --- Geographic coverage (1-degree lat/lng grid) ---
    geo_grid: dict[str, int] = {}
    for cam in cameras:
        if cam.location is not None:
            shape = to_shape(cam.location)
            key = f"{int(shape.y)},{int(shape.x)}"  # lat,lng rounded to 1-degree
            geo_grid[key] = geo_grid.get(key, 0) + 1

    # --- Summary ---
    total = len(cameras)
    total_online = sum(d["online"] for d in dept_stats.values())
    total_offline = sum(d["offline"] for d in dept_stats.values())
    total_unknown = sum(d["unknown"] for d in dept_stats.values())

    return {
        "per_department": dept_stats,
        "stale_cameras": stale,
        "stale_count": len(stale),
        "missing_departments": missing,
        "missing_department_count": len(missing),
        "geographic_coverage": geo_grid,
        "summary": {
            "total_cameras": total,
            "online": total_online,
            "offline": total_offline,
            "unknown": total_unknown,
            "coverage_pct": round((total_online / total) * 100, 1) if total > 0 else 0.0,
            "departments_onboarded": len(onboarded_depts),
            "departments_missing": len(missing),
        },
    }


@router.get(
    "/{camera_id}",
    response_model=schemas.CameraRead,
    summary="Get a single camera by its camera_id",
)
def get_camera(camera_id: str, db: Session = Depends(get_db)):
    camera = db.query(models.Camera).filter_by(camera_id=camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="camera not found")
    return camera


@router.delete("/{camera_id}", status_code=204, summary="Remove a camera from the registry")
def delete_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """
    Admin only - added so stray/test cameras can be cleared via the API
    instead of raw SQL (needed twice already this session). Refuses to
    delete a camera with recorded detections rather than cascading -
    those are kept intentionally as the historical source of truth
    (PLAN.md Section 2); decommissioning a camera shouldn't silently
    erase what it already saw.
    """
    camera = db.query(models.Camera).filter_by(camera_id=camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="camera not found")

    has_detections = db.query(models.Detection).filter_by(camera_id=camera_id).first() is not None
    if has_detections:
        raise HTTPException(
            status_code=409,
            detail="camera has recorded detections - those are kept as historical record, so this camera can't be deleted while they exist",
        )

    db.delete(camera)
    db.commit()
    log_action(db, actor, "camera.delete", target_type="camera", target_id=camera_id)
    return None

