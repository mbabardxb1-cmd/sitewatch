"""Data retention, driven by system settings (Settings page) with safe bounds.

What is removed:
* raw uptime checks older than N days (hourly rollups are refreshed first and kept indefinitely,
  so long-term charts and report history survive);
* audit runs (and their findings) older than N months, always keeping each website's latest run;
* notification delivery logs older than N days;
* sessions that expired more than 30 days ago.
Never removed automatically: monthly reports, issues, incidents, maintenance entries, the
security audit log.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.models import AuditRun, NotificationLog, SystemSetting, UptimeCheck, UserSession
from app.monitoring import stats

# key: (default, minimum, maximum)
SYSTEM_SETTINGS: dict[str, tuple[int, int, int]] = {
    "reports.generation_day": (2, 1, 28),
    "retention.uptime_checks_days": (400, 62, 3650),  # >= 2 months so monthly comparisons have raw data
    "retention.audit_runs_months": (24, 3, 120),
    "retention.notification_logs_days": (365, 30, 3650),
}


def setting_int(db: Session, key: str) -> int:
    default, lo, hi = SYSTEM_SETTINGS[key]
    row = db.get(SystemSetting, key)
    try:
        value = int(row.value) if row is not None and row.value is not None else default
    except (TypeError, ValueError):
        value = default
    return min(max(value, lo), hi)


def run_retention(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    out: dict[str, int] = {}

    cutoff = now - timedelta(days=setting_int(db, "retention.uptime_checks_days"))
    oldest = db.scalar(select(func.min(UptimeCheck.checked_at)).where(UptimeCheck.checked_at < cutoff))
    if oldest is not None:  # make sure every hour about to be deleted is rolled up first
        hours = int((cutoff - oldest).total_seconds() // 3600) + 2
        stats.rollup(db, hours_back=hours, now=cutoff + timedelta(hours=1))
    out["uptime_checks"] = db.execute(delete(UptimeCheck).where(UptimeCheck.checked_at < cutoff)).rowcount or 0

    months = setting_int(db, "retention.audit_runs_months")
    audit_cutoff = now - timedelta(days=31 * months)
    # each website's most recent run is always kept, however old
    keep = (select(AuditRun.id).ext(distinct_on(AuditRun.website_id))
            .order_by(AuditRun.website_id, AuditRun.created_at.desc()))
    out["audit_runs"] = db.execute(delete(AuditRun).where(
        AuditRun.created_at < audit_cutoff, AuditRun.id.not_in(keep.scalar_subquery()),
        AuditRun.status.in_(("completed", "partial", "failed")))).rowcount or 0

    log_cutoff = now - timedelta(days=setting_int(db, "retention.notification_logs_days"))
    out["notification_logs"] = db.execute(delete(NotificationLog).where(
        NotificationLog.created_at < log_cutoff, NotificationLog.status != "pending")).rowcount or 0

    out["sessions"] = db.execute(delete(UserSession).where(
        UserSession.expires_at < now - timedelta(days=30))).rowcount or 0
    db.commit()
    return out
