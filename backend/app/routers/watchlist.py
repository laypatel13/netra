"""
Watchlist management and match-checking.

Uses a representative dataset only — no real VAHAN/SARTHI/eGujCop/AFIS/NAFIS
integration in this build (see plan.md Section 4 and Section 9). That's
explicitly permitted by the official rules; only mention integration
readiness for those systems in the HLD document, don't build it.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
logger = logging.getLogger("netra.watchlist")


@router.post("", response_model=schemas.WatchlistRead)
def add_watchlist_entry(entry: schemas.WatchlistCreate, db: Session = Depends(get_db)):
    if db.query(models.WatchlistEntry).filter_by(plate_number=entry.plate_number).first():
        raise HTTPException(status_code=409, detail="plate already on watchlist")

    db_entry = models.WatchlistEntry(
        plate_number=entry.plate_number,
        category=entry.category,
        source=entry.source or "representative-dataset",
    )
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    return db_entry


@router.get("", response_model=List[schemas.WatchlistRead])
def list_watchlist(db: Session = Depends(get_db)):
    return db.query(models.WatchlistEntry).all()


@router.get("/check/{plate_number}", response_model=schemas.WatchlistMatch)
def check_plate(plate_number: str, db: Session = Depends(get_db)):
    return check_plate_against_watchlist(plate_number, db)


def check_plate_against_watchlist(plate_number: str, db: Session) -> schemas.WatchlistMatch:
    """
    Shared matching logic — called both by the manual /check endpoint and
    automatically from detections.py whenever a new plate is recorded.

    TODO (Day 5): wire a real match into a push/websocket alert to the
    control-room dashboard instead of just logging it.
    """
    entry: Optional[models.WatchlistEntry] = (
        db.query(models.WatchlistEntry).filter_by(plate_number=plate_number).first()
    )
    if entry:
        logger.warning("WATCHLIST ALERT: plate %s matched (%s)", plate_number, entry.category)
        return schemas.WatchlistMatch(matched=True, entry=entry)
    return schemas.WatchlistMatch(matched=False, entry=None)
