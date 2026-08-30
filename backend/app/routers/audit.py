"""
Audit log — read endpoint plus the log_action() helper other routers call.

Import log_action from here in cameras.py etc. rather than duplicating the
write logic — one place to change if the audit schema grows.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])


def log_action(
    db: Session,
    actor: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    entry = models.AuditLog(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )
    db.add(entry)
    db.commit()


@router.get("", response_model=List[schemas.AuditLogRead], summary="List audit log entries (admin only)")
def list_audit_log(db: Session = Depends(get_db), _role: str = Depends(require_admin)):
    return db.query(models.AuditLog).order_by(models.AuditLog.created_at.desc()).all()
