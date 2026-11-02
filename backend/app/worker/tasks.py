import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.auth.ratelimit import get_redis
from app.config import get_settings
from app.db import SessionLocal
from app.models import Client, NotificationLog, Website
from app.monitoring import engine, stats
from app.monitoring.checker import run_check
from app.notify.senders import NotifierError, get_notifier
from app.worker.celery_app import celery

log = logging.getLogger("sitewatch.worker")
HEARTBEAT_KEY = "sitewatch:worker:heartbeat"
BEAT_KEY = "sitewatch:beat:last_dispatch"


def claim_due_websites(db, limit: int = 500, now: datetime | None = None) -> list[uuid.UUID]:
    """Claim due websites atomically and push their next_check_at forward (a lease), so no other
    dispatcher can claim them. The check task sets the real next time when it finishes."""
    now = now or datetime.now(UTC)
    rows = db.execute(
        select(Website)
        .join(Client, Client.id == Website.client_id)
        .where(Website.status == "active", Client.is_active.is_(True),
               (Website.next_check_at.is_(None)) | (Website.next_check_at <= now))
        .order_by(Website.next_check_at.asc().nulls_first())
        .limit(limit)
        .with_for_update(of=Website, skip_locked=True)
    ).scalars().unique().all()
    for site in rows:
        site.next_check_at = now + timedelta(seconds=site.check_interval_s)
    db.commit()
    return [s.id for s in rows]


@celery.task
def dispatch_due_checks() -> int:
    with SessionLocal() as db:
        ids = claim_due_websites(db)
    for wid in ids:
        run_uptime_check.delay(str(wid))
    try:
        get_redis().set(BEAT_KEY, time.time())
    except Exception:  # noqa: BLE001
        pass
    return len(ids)


def perform_check(website_id: uuid.UUID, manual: bool = False) -> engine.Outcome | None:
    with SessionLocal() as db:
        site = db.get(Website, website_id)
        if site is None or site.status == "archived" or (site.status != "active" and not manual):
            return None
        url, timeout, codes, degraded = (site.base_url, site.timeout_s, list(site.expected_status_codes),
                                         site.degraded_threshold_ms)
    # Network I/O happens without holding any database lock.
    result = run_check(url, timeout, codes, degraded)
    checked_at = datetime.now(UTC)
    with SessionLocal() as db:
        site = db.scalar(select(Website).where(Website.id == website_id).with_for_update(of=Website))
        if site is None or site.base_url != url:  # deleted or URL changed while checking: discard
            return None
        outcome = engine.apply_result(db, site, result, checked_at, manual=manual)
        db.commit()
    for log_id in outcome.notifications:
        send_notification.delay(log_id)
    return outcome


@celery.task(bind=True, max_retries=0)
def run_uptime_check(self, website_id: str, manual: bool = False) -> None:
    perform_check(uuid.UUID(website_id), manual=manual)


@celery.task(bind=True, max_retries=4, default_retry_delay=60)
def send_notification(self, log_id: int) -> str:
    with SessionLocal() as db:
        entry = db.get(NotificationLog, log_id, with_for_update=True)
        if entry is None or entry.status != "pending":
            return "skipped"
        try:
            get_notifier(entry.channel).send(entry.recipients, entry.subject or "", entry.body or "")
        except NotifierError as e:
            entry.error = str(e)[:1000]
            if self.request.retries >= self.max_retries or get_settings().celery_eager:
                entry.status = "failed"
                db.commit()
                return "failed"
            db.commit()
            raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries)) from e
        entry.status = "sent"
        entry.error = None
        entry.sent_at = datetime.now(UTC)
        db.commit()
        return "sent"


@celery.task
def requeue_pending_notifications() -> int:
    """Safety net for notifications whose delivery task was lost (e.g. broker outage)."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(NotificationLog.id).where(
            NotificationLog.status == "pending",
            NotificationLog.created_at < datetime.now(UTC) - timedelta(minutes=10),
            NotificationLog.created_at > datetime.now(UTC) - timedelta(days=1))))
    for i in ids:
        send_notification.delay(i)
    return len(ids)


@celery.task
def heartbeat() -> None:
    get_redis().set(HEARTBEAT_KEY, time.time())


@celery.task
def rollup_metrics() -> None:
    with SessionLocal() as db:
        stats.rollup(db, hours_back=3)
        db.commit()


@celery.task
def purge_old_checks() -> int:
    """Delete raw checks past retention. Hourly rollups are refreshed first so history survives."""
    from app.models import UptimeCheck

    days = get_settings().uptime_check_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with SessionLocal() as db:
        stats.rollup(db, hours_back=24 * 2, now=cutoff + timedelta(days=1))
        n = db.execute(delete(UptimeCheck).where(UptimeCheck.checked_at < cutoff)).rowcount
        db.commit()
    log.info("Purged %s uptime checks older than %s days", n, days)
    return n or 0


# ---- Technical audits ---------------------------------------------------------------------

@celery.task(soft_time_limit=2 * 3600, time_limit=2 * 3600 + 300, acks_late=False)
def run_audit(run_id: str) -> str | None:
    """acks_late is off: a crawl must not be re-delivered and run twice after a worker restart;
    a run left 'running' is failed by fail_stuck_audits instead."""
    from app.audit.service import execute_run

    with SessionLocal() as db:
        run = execute_run(db, uuid.UUID(run_id))
        return run.status if run else None


@celery.task
def schedule_audits() -> int:
    from app.audit.service import create_due_scheduled_runs

    from app.models import AuditRun

    with SessionLocal() as db:
        ids = create_due_scheduled_runs(db)
        # Re-send runs whose task message was lost; claim_run() makes duplicates harmless.
        stale = list(db.scalars(select(AuditRun.id).where(
            AuditRun.status == "queued", AuditRun.created_at < datetime.now(UTC) - timedelta(minutes=15))))
    for rid in ids + [s for s in stale if s not in ids]:
        run_audit.delay(str(rid))
    return len(ids)


@celery.task
def fail_stuck_audits() -> int:
    from app.audit.service import fail_stuck_runs

    with SessionLocal() as db:
        return fail_stuck_runs(db)
