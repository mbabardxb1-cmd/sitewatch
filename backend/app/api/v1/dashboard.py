"""Dashboard summary. All figures are computed from stored data within the user's scope;
when no data exists the value is null (shown as "No data" in the UI), never a guess."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import require
from app.auth.scope import website_scope_clause
from app.db import get_db
from app.models import UptimeCheck, UptimeIncident, User, Website
from app.schemas.websites import WebsiteClientView, WebsiteStaffView
from app.services import website_service as svc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardSummary(BaseModel):
    total_websites: int
    online: int
    offline: int
    degraded: int
    unknown: int
    paused: int
    avg_response_ms_24h: int | None
    checks_24h: int
    monthly_uptime_pct: float | None
    monthly_uptime_note: str
    active_incidents: int
    websites: list[WebsiteStaffView | WebsiteClientView]


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db), user: User = Depends(require("website.read"))):
    scope = website_scope_clause(user)
    sites = list(db.scalars(
        select(Website).where(scope, Website.status != "archived").order_by(Website.name)
    ).unique())
    ids = [s.id for s in sites]
    active = [s for s in sites if s.status == "active"]

    def count(state: str) -> int:
        return sum(1 for s in active if s.current_state == state)

    since = datetime.now(UTC) - timedelta(hours=24)
    avg_ms, n_checks = (None, 0)
    open_incidents = 0
    if ids:
        avg_ms, n_checks = db.execute(
            select(func.avg(UptimeCheck.response_ms), func.count(UptimeCheck.id)).where(
                UptimeCheck.website_id.in_(ids), UptimeCheck.checked_at >= since,
                UptimeCheck.ok.is_(True), UptimeCheck.response_ms.is_not(None),
            )
        ).one()
        open_incidents = db.scalar(select(func.count()).select_from(UptimeIncident).where(
            UptimeIncident.website_id.in_(ids), UptimeIncident.ended_at.is_(None))) or 0

    return DashboardSummary(
        total_websites=len(sites),
        online=count("up"),
        offline=count("down"),
        degraded=count("degraded"),
        unknown=count("unknown"),
        paused=sum(1 for s in sites if s.status == "paused"),
        avg_response_ms_24h=round(avg_ms) if avg_ms is not None else None,
        checks_24h=n_checks or 0,
        # Uptime % is calculated by the monitoring service (phase 3). Until checks exist we
        # report no value rather than assuming 100 %.
        monthly_uptime_pct=None,
        monthly_uptime_note="Uptime history not yet available",
        active_incidents=open_incidents,
        websites=[svc.to_view(s, user) for s in sites],
    )
