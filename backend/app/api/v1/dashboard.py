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
from app.models import Client, DomainRecord, Issue, MonthlyReport, UptimeCheck, UptimeIncident, User, Website
from app.monitoring.stats import aggregate, uptime_for_sites
from app.schemas.websites import WebsiteClientView, WebsiteStaffView
from app.services import website_service as svc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class Notice(BaseModel):
    level: str  # critical | warning | info
    kind: str
    website_id: str | None
    website_name: str | None
    text: str
    link: str | None = None


class LatestReport(BaseModel):
    website_id: str
    report_id: str
    period: str
    kind: str
    published: bool
    overall_level: str | None


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
    uptime_30d: dict[str, float | None]
    open_issues: dict[str, dict[str, int]]
    audit_state: dict[str, dict]
    notices: list[Notice]
    latest_reports: list[LatestReport]


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db), user: User = Depends(require("website.read"))):
    scope = website_scope_clause(user)
    sites = list(db.scalars(
        select(Website).where(scope, Website.status != "archived").order_by(Website.name)
    ).unique())
    ids = [s.id for s in sites]
    active = [s for s in sites if s.status == "active"]

    def effective(site) -> str:
        # Same rule as the status label: stale "up"/"degraded" results are no longer current.
        return "unknown" if svc.is_stale(site) and site.current_state != "down" else site.current_state

    def count(state: str) -> int:
        return sum(1 for s in active if effective(s) == state)

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

    now = datetime.now(UTC)
    month = uptime_for_sites(db, ids, now - timedelta(days=30), now)
    return DashboardSummary(
        total_websites=len(sites),
        online=count("up"),
        offline=count("down"),
        degraded=count("degraded"),
        unknown=count("unknown"),
        paused=sum(1 for s in sites if s.status == "paused"),
        avg_response_ms_24h=round(avg_ms) if avg_ms is not None else None,
        checks_24h=n_checks or 0,
        monthly_uptime_pct=aggregate(list(month.values())),
        monthly_uptime_note=("Last 30 days, all websites combined" if aggregate(list(month.values())) is not None
                             else "No uptime history yet"),
        active_incidents=open_incidents,
        websites=[svc.to_view(s, user) for s in sites],
        uptime_30d={str(k): v.uptime_pct for k, v in month.items()},
        open_issues=_open_issues(db, ids, user),
        audit_state=_audit_state(db, ids),
        notices=_notices(db, sites, user, now),
        latest_reports=_latest_reports(db, ids, user),
    )


def _open_issues(db: Session, ids, user: User) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if not ids:
        return out
    q = select(Issue.website_id, Issue.severity, func.count()).where(
        Issue.website_id.in_(ids), Issue.status != "resolved").group_by(Issue.website_id, Issue.severity)
    if svc.is_client_user(user):
        q = q.where(Issue.severity != "info")
    for wid, sev, n in db.execute(q):
        out.setdefault(str(wid), {})[sev] = n
    return out


def _audit_state(db: Session, ids) -> dict[str, dict]:
    """Latest finished audit per site, so "no open issues" is never shown without evidence."""
    out = {str(i): {"last_audit_at": None, "reached_site": None} for i in ids}
    if not ids:
        return out
    from sqlalchemy.dialects.postgresql import distinct_on

    from app.models import AuditRun

    rows = db.execute(select(AuditRun.website_id, AuditRun.finished_at, AuditRun.pages_crawled)
                      .where(AuditRun.website_id.in_(ids), AuditRun.status.in_(("completed", "partial")))
                      .order_by(AuditRun.website_id, AuditRun.finished_at.desc())
                      .ext(distinct_on(AuditRun.website_id))).all()
    for wid, finished, pages in rows:
        out[str(wid)] = {"last_audit_at": finished, "reached_site": pages > 0}
    return out


def _latest_reports(db: Session, ids, user: User) -> list[LatestReport]:
    if not ids:
        return []
    q = select(MonthlyReport).where(MonthlyReport.website_id.in_(ids), MonthlyReport.status == "ready",
                                    MonthlyReport.kind == "client")
    if "report.read_all" not in user.permission_keys:
        q = q.where(MonthlyReport.approved_for_client.is_(True))
    latest: dict = {}
    for r in db.scalars(q.order_by(MonthlyReport.period.desc())):
        latest.setdefault(r.website_id, r)
    return [LatestReport(website_id=str(r.website_id), report_id=str(r.id), period=r.period, kind=r.kind,
                         published=r.approved_for_client,
                         overall_level=((r.data_snapshot or {}).get("status") or {}).get("level")) for r in latest.values()]


def _notices(db: Session, sites: list[Website], user: User, now: datetime) -> list[Notice]:
    from app.expiry import service as expiry

    client_user = svc.is_client_user(user)
    out: list[Notice] = []
    for site in sites:
        link = f"/websites/{site.id}"
        inc = db.scalar(select(UptimeIncident).where(UptimeIncident.website_id == site.id,
                                                     UptimeIncident.ended_at.is_(None)))
        if inc is not None:
            text = f"{site.name} is unavailable since {inc.started_at:%d %b %H:%M} UTC"
            if not client_user and inc.cause_summary:
                text += f" ({inc.cause_summary})"
            out.append(Notice(level="critical", kind="outage", website_id=str(site.id), website_name=site.name,
                              text=text, link=link))
        expiry_visible = "expiry.read" in user.permission_keys and (
            not client_user or bool(db.get(Client, site.client_id).show_expiry_to_client))
        if expiry_visible:
            items = [("Domain", expiry.domain_effective(db.scalar(select(DomainRecord).where(
                         DomainRecord.website_id == site.id))), 14, 30),
                     ("Hosting", expiry.hosting_effective(expiry.get_hosting_record(db, site)), 14, 30)]
            if site.ssl_monitoring and site.base_url.startswith("https://"):
                row = expiry.latest_ssl(db, site.id, completed_only=True)
                if row is not None and row.valid is False:
                    out.append(Notice(level="critical", kind="ssl_invalid", website_id=str(site.id),
                                      website_name=site.name, text=f"{site.name}: SSL certificate is not valid",
                                      link=link))
                else:
                    items.append(("SSL certificate", expiry.ssl_effective(row), 7, 30))
            for label, eff, crit, warn in items:
                dl = eff.days_left(now.date())
                if dl is not None and dl <= warn:
                    when = "has expired" if dl < 0 else f"expires in {dl} day{'s' if dl != 1 else ''}"
                    manual = " (manually entered date)" if eff.source == "manual" and not client_user else ""
                    out.append(Notice(level="critical" if dl <= crit else "warning", kind="expiry",
                                      website_id=str(site.id), website_name=site.name,
                                      text=f"{site.name}: {label} {when}{manual}", link=link))
        serious = db.scalar(select(func.count()).select_from(Issue).where(
            Issue.website_id == site.id, Issue.status != "resolved", Issue.severity.in_(("critical", "high"))))
        if serious:
            out.append(Notice(level="warning", kind="issues", website_id=str(site.id), website_name=site.name,
                              text=f"{site.name}: {serious} high-priority issue{'s' if serious != 1 else ''} outstanding",
                              link="/issues"))
    ids = [s.id for s in sites]
    stale = [s for s in sites if svc.is_stale(s, now)]
    if stale and not client_user:
        out.append(Notice(level="critical", kind="monitoring_overdue", website_id=None, website_name=None,
                          text=f"Uptime checks are overdue for {len(stale)} website{'s' if len(stale) != 1 else ''} "
                               "— check that the monitoring worker and scheduler are running", link="/uptime"))
    if ids and client_user:
        for r in db.scalars(select(MonthlyReport).where(
                MonthlyReport.website_id.in_(ids), MonthlyReport.kind == "client", MonthlyReport.status == "ready",
                MonthlyReport.approved_for_client.is_(True), MonthlyReport.approved_at >= now - timedelta(days=30))):
            name = next(s.name for s in sites if s.id == r.website_id)
            out.append(Notice(level="info", kind="report", website_id=str(r.website_id), website_name=name,
                              text=f"{name}: the {datetime.strptime(r.period, '%Y-%m'):%B %Y} report is available",
                              link="/reports"))
    if ids and "report.approve" in user.permission_keys:
        waiting = db.scalar(select(func.count()).select_from(MonthlyReport).where(
            MonthlyReport.website_id.in_(ids), MonthlyReport.kind == "client", MonthlyReport.status == "ready",
            MonthlyReport.approved_for_client.is_(False)))
        if waiting:
            out.append(Notice(level="info", kind="approval", website_id=None, website_name=None,
                              text=f"{waiting} client report{'s' if waiting != 1 else ''} awaiting publication",
                              link="/reports"))
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda n: order[n.level])
