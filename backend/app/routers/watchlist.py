"""
Watchlist management and match-checking.

Uses a representative dataset only — no real VAHAN/SARTHI/eGujCop/AFIS/NAFIS
integration in this build (see PLAN.md Section 4 and Section 9). That's
explicitly permitted by the official rules; only mention integration
readiness for those systems in the HLD document, don't build it.

An entry needs plate_number OR (vehicle_type AND vehicle_color), not
neither (enforced in schemas.py). Attribute-based entries exist for the
"suspect vehicle, no known plate" case (PLAN.md Section 0b) — a match on
one is a narrowing tool, not identification, so it's always returned at a
lower tier ("attributes") than an exact plate match ("exact_plate").
"""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import get_actor, require_admin
from app.serializers import detection_to_read

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
logger = logging.getLogger("netra.watchlist")


@router.post("", response_model=schemas.WatchlistRead, summary="Add a vehicle to the watchlist")
def add_watchlist_entry(entry: schemas.WatchlistCreate, db: Session = Depends(get_db)):
    if entry.plate_number and db.query(models.WatchlistEntry).filter_by(plate_number=entry.plate_number).first():
        raise HTTPException(status_code=409, detail="plate already on watchlist")

    db_entry = models.WatchlistEntry(
        plate_number=entry.plate_number,
        vehicle_type=entry.vehicle_type,
        vehicle_color=entry.vehicle_color,
        category=entry.category,
        source=entry.source or "representative-dataset",
    )
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    return db_entry


@router.delete(
    "/{entry_id}", status_code=204, summary="Remove a watchlist entry",
)
def delete_watchlist_entry(
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _role: str = Depends(require_admin),
):
    """
    Admin only — added so synthetic/test watchlist entries can be cleared
    via the API instead of raw SQL (needed twice already this session for
    stray test cameras/detections).
    """
    entry = db.query(models.WatchlistEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="watchlist entry not found")
    db.delete(entry)
    db.commit()
    return None


@router.get("", response_model=List[schemas.WatchlistRead], summary="List all watchlist entries")
def list_watchlist(db: Session = Depends(get_db)):
    return db.query(models.WatchlistEntry).all()


@router.get("/check/{plate_number}", response_model=schemas.WatchlistMatch, summary="Check if a plate is on the watchlist")
def check_plate(plate_number: str, db: Session = Depends(get_db)):
    return check_detection_against_watchlist(plate_number, None, None, db)


@router.get("/alerts/recent", response_model=List[schemas.WatchlistAlert], summary="Recent detections that matched the watchlist")
def recent_alerts(
    limit: int = Query(default=50, le=200),
    scan: int = Query(default=500, le=2000, description="How many recent detections to scan for matches"),
    db: Session = Depends(get_db),
):
    """
    Short-poll this from the Dashboard for a real-time-ish alert feed
    (PLAN.md Section 0c — no websocket layer needed at this scale).
    Recomputed on demand from the last `scan` detections rather than a
    separate persisted alerts table — simplest thing that works at
    hackathon scale, and watchlist entries can change after a detection
    was recorded without going stale.
    """
    entries = db.query(models.WatchlistEntry).all()
    plate_entries = {w.plate_number: w for w in entries if w.plate_number}
    attr_entries = {
        (w.vehicle_type, w.vehicle_color): w
        for w in entries
        if not w.plate_number and w.vehicle_type and w.vehicle_color
    }

    recent = (
        db.query(models.Detection)
        .order_by(models.Detection.created_at.desc())
        .limit(scan)
        .all()
    )

    alerts: List[schemas.WatchlistAlert] = []
    for d in recent:
        entry, tier = None, None
        if d.plate_number and d.plate_number in plate_entries:
            entry, tier = plate_entries[d.plate_number], "exact_plate"
        elif d.vehicle_type and d.vehicle_color and (d.vehicle_type, d.vehicle_color) in attr_entries:
            entry, tier = attr_entries[(d.vehicle_type, d.vehicle_color)], "attributes"

        if entry:
            alerts.append(schemas.WatchlistAlert(
                detection=detection_to_read(d),
                tier=tier,
                watchlist_entry=schemas.WatchlistRead.model_validate(entry),
            ))
        if len(alerts) >= limit:
            break

    return alerts


def check_detection_against_watchlist(
    plate_number: Optional[str],
    vehicle_type: Optional[str],
    vehicle_color: Optional[str],
    db: Session,
) -> schemas.WatchlistMatch:
    """
    Shared matching logic — called both by the manual /check endpoint and
    automatically from detections.py whenever a new detection is recorded.
    Checks an exact-plate entry first (precise); falls back to an
    attribute-only entry (type+color, coarse — a narrowing tool, not
    identification, see module docstring) only when there's no plate match.
    """
    if plate_number:
        entry: Optional[models.WatchlistEntry] = (
            db.query(models.WatchlistEntry).filter_by(plate_number=plate_number).first()
        )
        if entry:
            logger.warning("WATCHLIST ALERT (exact plate): %s matched (%s)", plate_number, entry.category)
            return schemas.WatchlistMatch(matched=True, tier="exact_plate", entry=entry)

    if vehicle_type and vehicle_color:
        entry = (
            db.query(models.WatchlistEntry)
            .filter(
                models.WatchlistEntry.plate_number.is_(None),
                models.WatchlistEntry.vehicle_type == vehicle_type,
                models.WatchlistEntry.vehicle_color == vehicle_color,
            )
            .first()
        )
        if entry:
            logger.warning(
                "WATCHLIST ALERT (attributes — narrowing match, not identification): %s %s matched (%s)",
                vehicle_color, vehicle_type, entry.category,
            )
            return schemas.WatchlistMatch(matched=True, tier="attributes", entry=entry)

    return schemas.WatchlistMatch(matched=False, tier=None, entry=None)
