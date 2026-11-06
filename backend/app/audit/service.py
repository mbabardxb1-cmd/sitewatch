"""Audit lifecycle: scheduling, execution, persistence and issue tracking.

Issue rules (requirement: never claim a fix that was not verified):
* A confirmed/warning finding creates an Issue keyed by fingerprint, or updates the existing one.
* A previously *resolved* issue that is observed again is re-opened and counted as recurring.
* An open issue becomes ``resolved`` ONLY when this run actually re-checked the same subject and
  did not observe the problem, or when the crawl covered the whole site (coverage "performed",
  not "partial") and the URL is no longer referenced anywhere. The verifying run is recorded.
* Developers can mark issues acknowledged / in progress / "resolved – pending verification",
  but only an audit can set ``resolved``.
* ``could_not_check`` findings are kept with the run but never become issues.
"""
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit.checks import CATEGORIES, CHECKS, AuditOutcome, evaluate
from app.audit.crawler import Crawler
from app.models import AuditFinding, AuditRun, Client, Issue, IssueComment, MonitoredUrl, Website
from app.netsafe.http import BlockedTargetError, DnsResolutionError
from app.netsafe.tls import inspect_certificate
from app.notify.service import compose, queue_notification

log = logging.getLogger("sitewatch.audit")
OPEN_STATES = ("open", "acknowledged", "in_progress", "resolved_pending_verification")
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
URL_SCOPED_CHECKS = {"broken_internal_link", "broken_external_link", "broken_image"}


class AuditBusy(Exception):
    pass


def period_of(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


# ---- scheduling -------------------------------------------------------------------------

def create_due_scheduled_runs(db: Session, now: datetime | None = None) -> list:
    """Create this month's scheduled audit for every active website whose audit day has arrived.
    Idempotent: the partial unique index (website_id, period) WHERE trigger='scheduled' makes a
    second call (or a second beat) a no-op."""
    now = now or datetime.now(UTC)
    period = period_of(now)
    sites = db.scalars(
        select(Website).join(Client, Client.id == Website.client_id)
        .where(Website.status == "active", Client.is_active.is_(True), Website.auto_audit.is_(True),
               Website.audit_day_of_month <= now.day)
    ).unique().all()
    created = []
    for site in sites:
        # A website added after this month's audit day waits for next month: nothing runs
        # until a person starts an audit or the next scheduled day arrives.
        audit_day = datetime(now.year, now.month, site.audit_day_of_month, tzinfo=UTC)
        if site.created_at and site.created_at >= audit_day:
            continue
        stmt = pg_insert(AuditRun).values(
            website_id=site.id, period=period, trigger="scheduled", status="queued",
            config_snapshot=site.crawl_config,
        ).on_conflict_do_nothing(index_elements=["website_id", "period"],
                                 index_where=text("trigger = 'scheduled'")).returning(AuditRun.id)
        rid = db.execute(stmt).scalar()
        if rid is not None:
            created.append(rid)
    db.commit()
    return created


def create_manual_run(db: Session, site: Website, user_id, categories: list[str] | None = None) -> AuditRun:
    busy = db.scalar(select(AuditRun.id).where(AuditRun.website_id == site.id,
                                               AuditRun.status.in_(("queued", "running"))))
    if busy is not None:
        raise AuditBusy()
    config = dict(site.crawl_config)
    if categories is not None and set(categories) != set(CATEGORIES):
        config["categories"] = [c for c in CATEGORIES if c in categories]
    run = AuditRun(website_id=site.id, trigger="manual", triggered_by=user_id, status="queued",
                   config_snapshot=config)
    db.add(run)
    db.flush()
    return run


def fail_stuck_runs(db: Session, older_than: timedelta = timedelta(hours=3)) -> int:
    cutoff = datetime.now(UTC) - older_than
    n = db.execute(update(AuditRun).where(AuditRun.status == "running", AuditRun.started_at < cutoff)
                   .values(status="failed", finished_at=datetime.now(UTC),
                           error="Audit did not finish (worker stopped or timed out)")).rowcount
    db.commit()
    return n or 0


# ---- execution --------------------------------------------------------------------------

def claim_run(db: Session, run_id) -> AuditRun | None:
    """Atomically move queued -> running; a duplicate task delivery gets None."""
    rid = db.execute(update(AuditRun).where(AuditRun.id == run_id, AuditRun.status == "queued")
                     .values(status="running", started_at=datetime.now(UTC)).returning(AuditRun.id)).scalar()
    db.commit()
    return db.get(AuditRun, rid) if rid else None


def expected_duration_s(db: Session, website_id) -> float | None:
    """Duration of the last finished audit of this website, used for the first time estimate."""
    prev = db.execute(select(AuditRun.started_at, AuditRun.finished_at).where(
        AuditRun.website_id == website_id, AuditRun.status.in_(("completed", "partial")),
        AuditRun.started_at.is_not(None), AuditRun.finished_at.is_not(None))
        .order_by(AuditRun.created_at.desc()).limit(1)).first()
    return round((prev[1] - prev[0]).total_seconds(), 1) if prev else None


class ProgressWriter:
    """Persists crawler progress in its own short transactions, at most every 2 seconds."""

    def __init__(self, run_id, expected_s: float | None, min_interval: float = 2.0):
        self.run_id, self.expected_s, self.min_interval = run_id, expected_s, min_interval
        self._last = 0.0

    def __call__(self, p: dict) -> None:
        now = time.monotonic()
        if now - self._last < self.min_interval:
            return
        self._last = now
        from app.db import SessionLocal

        with SessionLocal() as s:
            s.execute(update(AuditRun).where(AuditRun.id == self.run_id, AuditRun.status == "running").values(
                progress={**p, "expected_s": self.expected_s, "updated_at": datetime.now(UTC).isoformat()}))
            s.commit()


def execute_run(db: Session, run_id, crawler_factory=Crawler, progress=None) -> AuditRun | None:
    run = claim_run(db, run_id)
    if run is None:
        return None
    site = db.get(Website, run.website_id)
    config = run.config_snapshot or site.crawl_config
    categories = set(config["categories"]) if config.get("categories") else None
    expected = expected_duration_s(db, site.id)
    run.progress = {"percent": 0, "phase": "Starting", "pages_crawled": 0, "expected_s": expected,
                    "updated_at": datetime.now(UTC).isoformat()}
    db.commit()
    try:
        important = [m.url for m in db.scalars(select(MonitoredUrl).where(
            MonitoredUrl.website_id == site.id, MonitoredUrl.is_important.is_(True)))]
        base_url, hostname, is_https = site.base_url, site.hostname, site.base_url.startswith("https://")
        db.commit()  # release the transaction during the (long) crawl
        cr = crawler_factory(base_url, config, important, categories=categories,
                             progress=progress or ProgressWriter(run_id, expected)).run()
        cert, cert_error = None, None
        if is_https and (categories is None or "security" in categories):
            try:
                cert = inspect_certificate(hostname, _port(base_url), timeout=float(config.get("timeout_s", 15)))
            except (BlockedTargetError, DnsResolutionError, OSError) as e:
                cert_error = str(e)[:300] or type(e).__name__
        outcome = evaluate(cr, config, cert, cert_error, is_https, categories=categories)
        notifications = persist_outcome(db, run, site, outcome, cr)
    except Exception as e:  # noqa: BLE001
        log.exception("Audit %s failed", run_id)
        db.rollback()
        run = db.get(AuditRun, run_id)
        run.status, run.error, run.finished_at = "failed", f"{type(e).__name__}: {e}"[:1000], datetime.now(UTC)
        db.commit()
        return run
    db.commit()
    from app.worker.tasks import send_notification

    for log_id in notifications:
        send_notification.delay(log_id)
    return run


def _port(url: str) -> int:
    from urllib.parse import urlsplit

    return urlsplit(url).port or 443


def persist_outcome(db: Session, run: AuditRun, site: Website, outcome: AuditOutcome, cr) -> list[int]:
    now = datetime.now(UTC)
    for f in outcome.findings:
        db.add(AuditFinding(audit_run_id=run.id, website_id=site.id, check_key=f.check_key, severity=f.severity,
                            certainty=f.certainty, title=f.title[:500], url=f.url, source_url=f.source_url,
                            detail=f.detail, fingerprint=f.fingerprint))
    _track_urls(db, site, cr, now)
    diff = sync_issues(db, run, site, outcome, cr)

    by_sev = {s: 0 for s in SEVERITY_ORDER}
    by_cert = {"confirmed": 0, "warning": 0, "could_not_check": 0}
    for f in outcome.findings:
        by_cert[f.certainty] += 1
        if f.certainty != "could_not_check":
            by_sev[f.severity] += 1
    # "partial" = the run finished but some checks were limited or could not run at all.
    nothing_crawled = outcome.stats["pages_crawled"] == 0
    limited = any(c["status"] == "partial" for c in outcome.coverage.values())
    run.status = "partial" if nothing_crawled or limited else "completed"
    run.pages_crawled = outcome.stats["pages_crawled"]
    run.check_coverage = outcome.coverage
    run.summary = {
        **outcome.stats, "by_severity": by_sev, "by_certainty": by_cert,
        "broken_internal_links": sum(1 for f in outcome.findings if f.check_key == "broken_internal_link" and f.certainty != "could_not_check"),
        "broken_external_links": sum(1 for f in outcome.findings if f.check_key == "broken_external_link" and f.certainty != "could_not_check"),
        **{k: [str(i) for i in v] for k, v in diff.items()},
        "counts": {k: len(v) for k, v in diff.items()},
    }
    if (run.config_snapshot or {}).get("categories"):
        run.summary["categories"] = sorted(run.config_snapshot["categories"])
    run.finished_at = now
    run.progress = {"percent": 100, "phase": "Finished", "pages_crawled": run.pages_crawled}
    site.last_audit_at = now

    notes: list[int] = []
    label = "scheduled monthly" if run.trigger == "scheduled" else "manual"
    subject, body = compose("audit_completed", site, [
        f"The {label} technical audit finished ({run.status}).",
        f"Pages crawled: {run.pages_crawled}",
        "Issues by severity: " + ", ".join(f"{k} {v}" for k, v in by_sev.items() if v) if any(by_sev.values()) else "No issues found.",
        f"New: {len(diff['new'])} · Recurring: {len(diff['recurring'])} · Resolved (verified): {len(diff['resolved'])}",
    ])
    lid = queue_notification(db, event_type="audit_completed", website=site, dedup_key=f"audit:{run.id}:completed",
                             subject=subject, body=body)
    if lid:
        notes.append(lid)
    serious_new = [i for i in db.scalars(select(Issue).where(Issue.id.in_(diff["new"] + diff["recurring"])))
                   if i.severity in ("critical", "high")] if diff["new"] or diff["recurring"] else []
    if serious_new:
        subject, body = compose("critical_audit_findings", site, [
            f"The audit found {len(serious_new)} new or recurring critical/high issue(s):", "",
            *[f"- [{i.severity.upper()}] {i.title}" for i in serious_new[:20]],
        ])
        lid = queue_notification(db, event_type="critical_audit_findings", website=site,
                                 dedup_key=f"audit:{run.id}:critical", subject=subject, body=body)
        if lid:
            notes.append(lid)
    return notes


def _track_urls(db: Session, site: Website, cr, now: datetime) -> None:
    """Remember discovered internal URLs; the homepage is always 'important'."""
    for url, page in list(cr.pages.items())[:5000]:
        if page.depth < 0:
            continue
        h = hashlib.sha256(url.encode()).hexdigest()
        stmt = pg_insert(MonitoredUrl).values(
            website_id=site.id, url=url[:2048], url_hash=h, is_important=(url == site.base_url),
            first_seen_at=now, last_seen_at=now, last_status=page.fetch.status,
        ).on_conflict_do_update(constraint="uq_monitored_urls_site_hash",
                                set_={"last_seen_at": now, "last_status": page.fetch.status})
        db.execute(stmt)


def sync_issues(db: Session, run: AuditRun, site: Website, outcome: AuditOutcome, cr) -> dict[str, list]:
    diff: dict[str, list] = {"new": [], "recurring": [], "persisting": [], "resolved": []}
    now = datetime.now(UTC)
    current = {f.fingerprint: f for f in outcome.findings if f.certainty != "could_not_check"}
    existing = {i.fingerprint: i for i in db.scalars(select(Issue).where(Issue.website_id == site.id))}

    for fp, f in current.items():
        issue = existing.get(fp)
        if issue is None:
            issue = Issue(website_id=site.id, fingerprint=fp, check_key=f.check_key, title=f.title[:500],
                          severity=f.severity, certainty=f.certainty, url=f.url, detail=f.detail, status="open",
                          first_seen_run_id=run.id, last_seen_run_id=run.id, occurrences=1)
            db.add(issue)
            db.flush()
            diff["new"].append(issue.id)
            continue
        issue.title, issue.severity, issue.certainty, issue.detail = f.title[:500], f.severity, f.certainty, f.detail
        issue.last_seen_run_id = run.id
        issue.occurrences += 1
        if issue.status == "resolved":
            issue.status, issue.resolved_at, issue.resolved_verified_run_id = "open", None, None
            issue.resolution_note = None
            issue.reopened_count += 1
            diff["recurring"].append(issue.id)
        else:
            if issue.status == "resolved_pending_verification":
                issue.status = "open"  # the fix did not hold: the audit still sees it
            diff["persisting"].append(issue.id)

    verified_fps: set[str] = set()
    for key, subjects in outcome.verified_subjects.items():
        for s in subjects:
            verified_fps.add(hashlib.sha256(f"{key}|{s}".encode()).hexdigest())
    seen_urls = set(cr.pages) | set(cr.external) | set(cr.images) | set(cr.link_sources) | set(cr.image_sources)

    for fp, issue in existing.items():
        if issue.status not in OPEN_STATES or fp in current:
            continue
        cov = outcome.coverage.get(issue.check_key, {})
        if cov.get("status") not in ("performed", "partial"):
            continue  # check not run this time: cannot verify anything
        note = None
        if fp in verified_fps:
            note = f"Re-checked by the audit on {now:%Y-%m-%d}; the problem was no longer observed."
        elif (issue.check_key in URL_SCOPED_CHECKS and cov.get("status") == "performed" and issue.url
              and issue.url not in seen_urls):
            note = (f"The audit on {now:%Y-%m-%d} crawled the site within its configured limits and found "
                    "no remaining references to this URL.")
        if note:
            issue.status, issue.resolved_at, issue.resolved_verified_run_id = "resolved", now, run.id
            issue.resolution_note = note
            diff["resolved"].append(issue.id)
    return diff


def delete_run(db: Session, run: AuditRun) -> int:
    """Delete an audit run. Issues first found by this run and not seen by any other run are
    deleted with it (with their notes); other issues simply lose the reference."""
    only_here = list(db.scalars(select(Issue.id).where(
        Issue.first_seen_run_id == run.id,
        (Issue.last_seen_run_id == run.id) | Issue.last_seen_run_id.is_(None))))
    if only_here:
        db.execute(IssueComment.__table__.delete().where(IssueComment.issue_id.in_(only_here)))
        db.execute(Issue.__table__.delete().where(Issue.id.in_(only_here)))
    db.execute(AuditFinding.__table__.delete().where(AuditFinding.audit_run_id == run.id))
    db.delete(run)
    db.flush()
    return len(only_here)


CHECK_LABELS = CHECKS
