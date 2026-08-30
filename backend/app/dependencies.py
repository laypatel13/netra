"""
Minimal RBAC — header-based role/department scoping.

Intentionally lightweight for hackathon speed, not a production auth system.
Swap for real JWT/session auth before anything resembling production. This
exists to demonstrate the "enhanced cybersecurity/RBAC/auditability" bonus
consideration item from plan.md Section 11, not to be a hardened auth layer.

Roles:
  admin      — can onboard/bulk-onboard/sync status, read audit log
  department — read-only, scoped to their own department (X-Department header)
  viewer     — read-only, unscoped (default if no X-Role header sent)
"""
from typing import Optional

from fastapi import Header, Depends, HTTPException

VALID_ROLES = ("admin", "department", "viewer")


def get_role(x_role: str = Header(default="viewer")) -> str:
    if x_role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"X-Role must be one of {VALID_ROLES}")
    return x_role


def get_department(x_department: Optional[str] = Header(default=None)) -> Optional[str]:
    return x_department


def get_actor(x_actor: str = Header(default="unknown")) -> str:
    """Who's making the request — for audit logging. Not authentication."""
    return x_actor


def require_admin(role: str = Depends(get_role)) -> str:
    if role != "admin":
        raise HTTPException(status_code=403, detail="admin role required (send X-Role: admin)")
    return role
