"""System settings (schedules, retention) and the security audit log. Administrators only."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.db import get_db
from app.models import AuditLog, SystemSetting, User
from app.services.audit_log import record
from app.services.retention import SYSTEM_SETTINGS, setting_int

router = APIRouter(prefix="/settings", tags=["settings"])
FIELD_TO_KEY = {
    "report_generation_day": "reports.generation_day",
    "uptime_check_retention_days": "retention.uptime_checks_days",
    "audit_run_retention_months": "retention.audit_runs_months",
    "notification_log_retention_days": "retention.notification_logs_days",
}


class SystemSettingsIO(BaseModel):
    report_generation_day: int | None = None
    uptime_check_retention_days: int | None = None
    audit_run_retention_months: int | None = None
    notification_log_retention_days: int | None = None


def _current(db: Session) -> dict:
    return {
        "values": {f: setting_int(db, k) for f, k in FIELD_TO_KEY.items()},
        "limits": {f: {"min": SYSTEM_SETTINGS[k][1], "max": SYSTEM_SETTINGS[k][2], "default": SYSTEM_SETTINGS[k][0]}
                   for f, k in FIELD_TO_KEY.items()},
    }


@router.get("/system")
def get_system(db: Session = Depends(get_db), _: User = Depends(require("settings.manage"))):
    return _current(db)


@router.put("/system")
def put_system(body: SystemSettingsIO, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("settings.manage"))):
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        key = FIELD_TO_KEY[field]
        _, lo, hi = SYSTEM_SETTINGS[key]
        if not lo <= value <= hi:
            raise HTTPException(422, f"{field} must be between {lo} and {hi}.")
    for field, value in changes.items():
        key = FIELD_TO_KEY[field]
        row = db.get(SystemSetting, key)
        if row is None:
            db.add(SystemSetting(key=key, value=value, updated_by=user.id))
        else:
            row.value, row.updated_by = value, user.id
    record(db, user.id, "settings.system", None, None, client_ip(request), **changes)
    db.commit()
    return _current(db)


class AuditLogOut(BaseModel):
    id: uuid.UUID
    at: datetime
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    detail: dict


@router.get("/audit-log", response_model=list[AuditLogOut])
def audit_log(action: str | None = Query(default=None, max_length=80), limit: int = Query(default=200, ge=1, le=1000),
              db: Session = Depends(get_db), _: User = Depends(require("settings.manage"))):
    q = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_id)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    rows = db.execute(q.order_by(AuditLog.at.desc()).limit(limit)).all()
    return [AuditLogOut(id=a.id, at=a.at, actor_email=e, action=a.action, target_type=a.target_type,
                        target_id=a.target_id, ip=a.ip, detail=a.detail or {}) for a, e in rows]
