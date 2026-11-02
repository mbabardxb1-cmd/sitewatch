"""Queue notifications with recipient resolution, de-duplication and rate limiting.

`queue_notification` only writes a `notification_logs` row inside the caller's transaction.
The caller enqueues delivery (`send_notification` task) for the returned id AFTER commit,
so a rolled-back transaction never sends mail.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import NotificationLog, NotificationRule, Website

EVENT_LABELS = {
    "website_down": "Website down",
    "website_recovered": "Website recovered",
    "repeated_server_errors": "Repeated server errors",
    "response_degraded": "Slow response times",
    "critical_audit_findings": "Critical audit findings",
    "domain_expiry": "Domain expiry approaching",
    "hosting_renewal": "Hosting renewal approaching",
    "ssl_expiry": "SSL certificate expiry approaching",
    "audit_completed": "Monthly audit completed",
    "report_available": "Monthly report available",
}


def resolve_recipients(db: Session, website: Website | None, event_type: str) -> tuple[list[str], list[NotificationRule]]:
    """Website-level recipients get every event for that site unless a site-specific rule for the
    event is disabled (a mute). Enabled rules (site, client or global scope) add recipients."""
    conds = [NotificationRule.event_type == event_type]
    if website is not None:
        conds.append(or_(
            NotificationRule.website_id == website.id,
            and_(NotificationRule.website_id.is_(None), NotificationRule.client_id == website.client_id),
            and_(NotificationRule.website_id.is_(None), NotificationRule.client_id.is_(None)),
        ))
    else:
        conds.append(and_(NotificationRule.website_id.is_(None), NotificationRule.client_id.is_(None)))
    rules = list(db.scalars(select(NotificationRule).where(*conds)))
    muted = website is not None and any(r.website_id == website.id and not r.enabled for r in rules)
    active = [r for r in rules if r.enabled]
    recipients: list[str] = []
    if website is not None and not muted:
        recipients += website.notification_recipients or []
    for r in active:
        recipients += r.recipients or []
    seen, out = set(), []
    for r in recipients:
        key = r.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(r.strip())
    return out, active


def queue_notification(db: Session, *, event_type: str, website: Website | None, dedup_key: str,
                       subject: str, body: str) -> int | None:
    """Record a notification. Returns the log id to deliver, or None if it was a duplicate,
    rate-limited, or had no recipients (those cases are still logged when new)."""
    recipients, rules = resolve_recipients(db, website, event_type)
    status, error = "pending", None
    if not recipients:
        status, error = "suppressed", "No recipients configured"
    else:
        min_interval = max((r.min_interval_s for r in rules), default=0)
        if min_interval and website is not None:
            recent = db.scalar(select(NotificationLog.id).where(
                NotificationLog.website_id == website.id, NotificationLog.event_type == event_type,
                NotificationLog.status == "sent",
                NotificationLog.created_at >= datetime.now(UTC) - timedelta(seconds=min_interval)).limit(1))
            if recent is not None:
                status, error = "suppressed", f"Rate limited (min interval {min_interval}s)"

    stmt = pg_insert(NotificationLog).values(
        rule_id=rules[0].id if rules else None, website_id=website.id if website else None,
        event_type=event_type, dedup_key=dedup_key[:255], channel="email", recipients=recipients,
        subject=subject[:500], body=body, status=status, error=error,
    ).on_conflict_do_nothing(constraint="uq_notification_logs_dedup").returning(NotificationLog.id)
    log_id = db.execute(stmt).scalar()
    if log_id is None or status != "pending":
        return None
    return log_id


def website_link(website: Website) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/websites/{website.id}"


def compose(event_type: str, website: Website, lines: list[str]) -> tuple[str, str]:
    subject = f"[SiteWatch] {EVENT_LABELS.get(event_type, event_type)}: {website.name}"
    body = "\n".join([
        f"{EVENT_LABELS.get(event_type, event_type)} — {website.name}",
        f"URL: {website.base_url}",
        "",
        *lines,
        "",
        f"Details: {website_link(website)}",
        "",
        "This is an automated message from SiteWatch monitoring.",
    ])
    return subject, body
