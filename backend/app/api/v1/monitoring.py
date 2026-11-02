"""Uptime statistics, check history, manual checks and incidents."""
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.auth.ratelimit import RateLimiter, get_redis
from app.auth.scope import get_website_or_404, not_found, website_scope_clause
from app.config import get_settings
from app.db import get_db
from app.models import UptimeCheck, UptimeIncident, User, Website
from app.monitoring.stats import response_series, standard_periods, uptime_for_sites
from app.services.audit_log import record
from app.services.website_service import is_client_user

router = APIRouter(tags=["monitoring"])


class UptimePeriod(BaseModel):
    period_start: datetime
    period_end: datetime
    uptime_pct: float | None
    monitored_s: int
    downtime_s: int
    unknown_s: int
    coverage_pct: float
    checks: int
    incidents: int


class UptimeSummary(BaseModel):
    website_id: uuid.UUID
    day: UptimePeriod
    week: UptimePeriod
    month: UptimePeriod
    calendar_month: UptimePeriod
    method: str = ("Time-weighted from recorded checks. Periods without checks are reported as unknown "
                   "and excluded; downtime is the duration of confirmed incidents.")


class ResponseBucket(BaseModel):
    bucket: datetime
    checks: int
    successes: int
    avg_ms: int | None
    p95_ms: int | None
    max_ms: int | None


class CheckOut(BaseModel):
    id: int
    checked_at: datetime
    ok: bool
    degraded: bool
    http_status: int | None
    response_ms: int | None
    error_type: str | None
    error_detail: str | None
    final_url: str | None
    attempt: int
    is_retry: bool
    is_manual: bool


class IncidentClientView(BaseModel):
    id: uuid.UUID
    website_id: uuid.UUID
    website_name: str
    started_at: datetime
    ended_at: datetime | None
    duration_s: int | None
    status: str


class IncidentStaffView(IncidentClientView):
    cause_summary: str
    first_check_id: int | None
    last_check_id: int | None
    acknowledged_by_email: str | None
    acknowledged_at: datetime | None


def _summary(db: Session, site_id: uuid.UUID) -> UptimeSummary:
    periods = standard_periods()
    out = {}
    for name, (start, end) in periods.items():
        out[name] = UptimePeriod(**uptime_for_sites(db, [site_id], start, end)[site_id].as_dict())
    return UptimeSummary(website_id=site_id, **out)


@router.get("/websites/{website_id}/uptime", response_model=UptimeSummary)
def website_uptime(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("website.read"))):
    site = get_website_or_404(db, user, website_id)
    return _summary(db, site.id)


class UptimeOverviewRow(BaseModel):
    website_id: uuid.UUID
    day: float | None
    week: float | None
    month: float | None
    month_coverage_pct: float
    avg_response_ms_24h: int | None


@router.get("/uptime/overview", response_model=list[UptimeOverviewRow])
def uptime_overview(db: Session = Depends(get_db), user: User = Depends(require("website.read"))):
    """Uptime for every website the user can see, in one call."""
    from sqlalchemy import func

    ids = list(db.scalars(select(Website.id).where(website_scope_clause(user), Website.status != "archived")))
    p = standard_periods()
    day, week, month = (uptime_for_sites(db, ids, *p[k]) for k in ("day", "week", "month"))
    avg = dict(db.execute(select(UptimeCheck.website_id, func.avg(UptimeCheck.response_ms)).where(
        UptimeCheck.website_id.in_(ids), UptimeCheck.ok.is_(True), UptimeCheck.checked_at >= p["day"][0]
    ).group_by(UptimeCheck.website_id)).all()) if ids else {}
    return [UptimeOverviewRow(website_id=i, day=day[i].uptime_pct, week=week[i].uptime_pct, month=month[i].uptime_pct,
                              month_coverage_pct=month[i].coverage_pct,
                              avg_response_ms_24h=round(avg[i]) if avg.get(i) is not None else None) for i in ids]


@router.get("/websites/{website_id}/response-times", response_model=list[ResponseBucket])
def website_response_times(website_id: uuid.UUID, hours: int = Query(default=24, ge=1, le=24 * 90),
                           db: Session = Depends(get_db), user: User = Depends(require("website.read"))):
    site = get_website_or_404(db, user, website_id)
    return response_series(db, site.id, hours)


@router.get("/websites/{website_id}/checks", response_model=list[CheckOut])
def website_checks(website_id: uuid.UUID, limit: int = Query(default=100, ge=1, le=1000),
                   before_id: int | None = None, only_failures: bool = False,
                   db: Session = Depends(get_db), user: User = Depends(require("uptime.read_detailed"))):
    site = get_website_or_404(db, user, website_id)
    q = select(UptimeCheck).where(UptimeCheck.website_id == site.id)
    if before_id:
        q = q.where(UptimeCheck.id < before_id)
    if only_failures:
        q = q.where(UptimeCheck.ok.is_(False))
    rows = db.scalars(q.order_by(UptimeCheck.id.desc()).limit(limit))
    return [CheckOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/websites/{website_id}/check-now", status_code=status.HTTP_202_ACCEPTED)
def check_now(website_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require("uptime.check_now"))):
    site = get_website_or_404(db, user, website_id)
    if site.status == "archived":
        raise HTTPException(status.HTTP_409_CONFLICT, "Archived websites are not monitored.")
    cooldown = get_settings().check_now_cooldown_s
    if not RateLimiter(get_redis()).hit(f"checknow:{site.id}", 1, cooldown):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"A manual check ran recently; wait {cooldown}s.")
    record(db, user.id, "uptime.check_now", "website", site.id, client_ip(request))
    db.commit()
    from app.worker.tasks import run_uptime_check

    run_uptime_check.delay(str(site.id), manual=True)
    return {"queued": True}


# ---- Incidents ------------------------------------------------------------------------

def _incident_view(inc: UptimeIncident, site: Website, user: User, db: Session):
    base = dict(id=inc.id, website_id=inc.website_id, website_name=site.name, started_at=inc.started_at,
                ended_at=inc.ended_at, duration_s=inc.duration_s, status=inc.status)
    if is_client_user(user):
        return IncidentClientView(**base)
    ack = db.get(User, inc.acknowledged_by) if inc.acknowledged_by else None
    return IncidentStaffView(**base, cause_summary=inc.cause_summary, first_check_id=inc.first_check_id,
                             last_check_id=inc.last_check_id, acknowledged_by_email=ack.email if ack else None,
                             acknowledged_at=inc.acknowledged_at)


@router.get("/incidents", response_model=list[IncidentStaffView | IncidentClientView])
def list_incidents(state: Literal["open", "resolved", "all"] = "all", website_id: uuid.UUID | None = None,
                   limit: int = Query(default=100, ge=1, le=500),
                   db: Session = Depends(get_db), user: User = Depends(require("incident.read"))):
    q = (select(UptimeIncident, Website).join(Website, Website.id == UptimeIncident.website_id)
         .where(website_scope_clause(user)))
    if state == "open":
        q = q.where(UptimeIncident.ended_at.is_(None))
    elif state == "resolved":
        q = q.where(UptimeIncident.ended_at.is_not(None))
    if website_id:
        q = q.where(UptimeIncident.website_id == website_id)
    rows = db.execute(q.order_by(UptimeIncident.ended_at.is_(None).desc(), UptimeIncident.started_at.desc())
                      .limit(limit)).unique().all()
    return [_incident_view(inc, site, user, db) for inc, site in rows]


def _get_incident(db: Session, user: User, incident_id: uuid.UUID) -> tuple[UptimeIncident, Website]:
    row = db.execute(select(UptimeIncident, Website).join(Website, Website.id == UptimeIncident.website_id)
                     .where(UptimeIncident.id == incident_id, website_scope_clause(user))).unique().first()
    if row is None:
        raise not_found()
    return row[0], row[1]


@router.get("/incidents/{incident_id}", response_model=IncidentStaffView | IncidentClientView)
def get_incident(incident_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("incident.read"))):
    inc, site = _get_incident(db, user, incident_id)
    return _incident_view(inc, site, user, db)


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentStaffView)
def acknowledge_incident(incident_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("incident.manage"))):
    inc, site = _get_incident(db, user, incident_id)
    if inc.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Incident is already resolved.")
    inc.status = "acknowledged"
    inc.acknowledged_by = user.id
    inc.acknowledged_at = datetime.now(UTC)
    record(db, user.id, "incident.acknowledge", "incident", inc.id, client_ip(request))
    db.commit()
    return _incident_view(inc, site, user, db)
