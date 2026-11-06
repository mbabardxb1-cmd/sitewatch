from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.permissions import CLIENT
from app.models import Client, User, Website
from app.netsafe.urls import UnsafeUrlError, normalize_site_url
from app.schemas.websites import (
    AssignedUser,
    WebsiteClientView,
    WebsiteCreate,
    WebsiteStaffView,
    WebsiteUpdate,
)


def is_client_user(user: User) -> bool:
    return CLIENT in user.role_keys


def _ago(delta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 120:
        return f"{mins} min ago"
    hours = mins // 60
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} days ago"


def is_stale(site: Website, now: datetime | None = None) -> bool:
    """An active site whose last check is older than 3 intervals (min. 15 min): the monitoring
    result can no longer be presented as current."""
    if site.status != "active" or site.last_checked_at is None:
        return False
    now = now or datetime.now(UTC)
    return (now - site.last_checked_at).total_seconds() > max(900, 3 * site.check_interval_s)


def health_of(site: Website, now: datetime | None = None) -> tuple[str, str]:
    """Map monitoring state to a display status plus the evidence behind it."""
    if site.status == "archived":
        return "unknown", "Archived — not monitored"
    if site.status == "paused":
        return "unknown", "Monitoring paused"
    if is_stale(site, now):
        ago = _ago((now or datetime.now(UTC)) - site.last_checked_at)
        if site.current_state == "down":
            return "critical", f"Outage confirmed; no checks since ({ago})"
        return "unknown", f"No recent checks (last check {ago}); monitoring may be interrupted"
    return {
        "up": ("healthy", "Responding normally at the last check"),
        "degraded": ("warning", "Responding, but slower than the configured threshold"),
        "down": ("critical", "Outage confirmed after retries"),
    }.get(site.current_state, ("unknown", "No completed checks yet"))


def to_view(site: Website, user: User) -> WebsiteClientView | WebsiteStaffView:
    health, reason = health_of(site)
    base = dict(
        id=site.id, name=site.name, base_url=site.base_url, client_id=site.client_id,
        client_name=site.client.name, status=site.status, current_state=site.current_state,
        health=health, health_reason=reason,
        last_checked_at=site.last_checked_at, last_audit_at=site.last_audit_at,
    )
    if is_client_user(user):
        return WebsiteClientView(**base)
    return WebsiteStaffView(
        **base,
        hostname=site.hostname, check_interval_s=site.check_interval_s, retry_attempts=site.retry_attempts,
        recovery_threshold=site.recovery_threshold, timeout_s=site.timeout_s,
        degraded_threshold_ms=site.degraded_threshold_ms, expected_status_codes=site.expected_status_codes,
        next_check_at=site.next_check_at, audit_day_of_month=site.audit_day_of_month, auto_audit=site.auto_audit,
        crawl_config=site.crawl_config, ssl_monitoring=site.ssl_monitoring,
        notification_recipients=site.notification_recipients,
        assigned_users=[
            AssignedUser(id=u.id, email=u.email, full_name=u.full_name, role=",".join(sorted(u.role_keys)))
            for u in sorted(site.assigned_users, key=lambda u: u.email)
        ],
        archived_at=site.archived_at, created_at=site.created_at, updated_at=site.updated_at,
    )


def _validated_url(raw: str) -> tuple[str, str]:
    try:
        return normalize_site_url(raw)
    except UnsafeUrlError as e:
        raise HTTPException(422, str(e)) from None


def _ensure_unique(db: Session, client_id, url: str, exclude_id=None) -> None:
    q = select(Website.id).where(Website.client_id == client_id, func.lower(Website.base_url) == url.lower(),
                                 Website.status != "archived")
    if exclude_id is not None:
        q = q.where(Website.id != exclude_id)
    if db.scalar(q) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This client already has an active website with that URL.")


def create_website(db: Session, actor: User, body: WebsiteCreate) -> Website:
    client = db.get(Client, body.client_id)
    if client is None or not client.is_active:
        raise HTTPException(422, "Client organization not found or inactive.")
    url, host = _validated_url(body.base_url)
    _ensure_unique(db, client.id, url)
    data = body.model_dump(exclude={"client_id", "base_url", "crawl_config"})
    site = Website(client_id=client.id, base_url=url, hostname=host, crawl_config=body.crawl_config.model_dump(),
                   next_check_at=datetime.now(UTC), **data)
    # A developer who registers a site is assigned to it, otherwise they could not see it.
    site.assigned_users = [] if "website.all" in actor.permission_keys else [actor]
    db.add(site)
    db.flush()
    db.refresh(site)
    return site


def update_website(db: Session, site: Website, body: WebsiteUpdate) -> list[str]:
    if site.status == "archived":
        raise HTTPException(status.HTTP_409_CONFLICT, "Archived websites cannot be edited; restore it first.")
    changes = body.model_dump(exclude_unset=True)
    for key in ("name", "base_url"):
        if key in changes and changes[key] is None:
            raise HTTPException(422, f"{key} cannot be empty.")
    if "base_url" in changes:
        url, host = _validated_url(changes.pop("base_url"))
        if url != site.base_url:
            _ensure_unique(db, site.client_id, url, exclude_id=site.id)
            site.base_url, site.hostname = url, host
            site.current_state = "unknown"  # previous results describe a different URL
            changes["base_url"] = url
    for key, value in changes.items():
        if key != "base_url":
            setattr(site, key, value)
    if changes.get("status") == "active" and site.next_check_at is None:
        site.next_check_at = datetime.now(UTC)
    return sorted(changes)


def archive(site: Website) -> None:
    site.status = "archived"
    site.archived_at = datetime.now(UTC)
    site.next_check_at = None


def restore(db: Session, site: Website) -> None:
    _ensure_unique(db, site.client_id, site.base_url, exclude_id=site.id)
    site.status = "paused"  # restored sites resume only when someone re-activates them
    site.archived_at = None


def set_assignments(db: Session, site: Website, user_ids) -> None:
    users = list(db.scalars(select(User).where(User.id.in_(user_ids)))) if user_ids else []
    if len(users) != len(set(user_ids)):
        raise HTTPException(422, "One or more users do not exist.")
    for u in users:
        if CLIENT in u.role_keys and u.client_id != site.client_id:
            raise HTTPException(422, f"{u.email} belongs to a different client organization.")
    site.assigned_users = users
