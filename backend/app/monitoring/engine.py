"""Uptime state machine. Applies one check result to a website (whose row the caller has locked).

States: unknown / up / degraded / down.

* A failed check while not down starts (or extends) a failure streak and schedules a quick retry
  (`retry_delay_s`). Only after `retry_attempts` retries also fail is the outage confirmed:
  an incident is opened, starting at the FIRST failed check, and one "down" alert is queued.
* While down, further failures update the incident but never re-alert.
* `recovery_threshold` consecutive successes close the incident and queue one recovery alert.
* `degraded_alert_after` consecutive slow responses queue one degradation alert per episode.
* `server_error_alert_threshold` 5xx responses within the window queue one alert per window
  (catches intermittent errors that never become a confirmed outage).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import UptimeCheck, UptimeIncident, Website
from app.monitoring.checker import CheckResult
from app.notify.service import compose, queue_notification


@dataclass
class Outcome:
    check: UptimeCheck
    notifications: list[int] = field(default_factory=list)
    opened_incident: UptimeIncident | None = None
    closed_incident: UptimeIncident | None = None


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def apply_result(db: Session, site: Website, r: CheckResult, checked_at: datetime, manual: bool = False) -> Outcome:
    cfg = get_settings()
    was_down = site.current_state == "down"
    retrying = not was_down and site.consecutive_failures > 0

    check = UptimeCheck(
        website_id=site.id, checked_at=checked_at, ok=r.ok, degraded=r.degraded, http_status=r.http_status,
        response_ms=r.response_ms, error_type=r.error_type, error_detail=r.error_detail, final_url=r.final_url,
        attempt=site.consecutive_failures + 1 if retrying else 1, is_retry=retrying, is_manual=manual,
    )
    db.add(check)
    db.flush()
    out = Outcome(check=check)
    site.last_checked_at = checked_at
    interval = timedelta(seconds=site.check_interval_s)
    next_at = checked_at + interval

    if r.ok:
        site.consecutive_failures = 0
        site.failure_streak_started_at = None
        site.consecutive_successes += 1
        if was_down:
            if site.consecutive_successes >= site.recovery_threshold:
                _close_incident(db, site, check, checked_at, out)
                site.current_state = "degraded" if r.degraded else "up"
        else:
            site.current_state = "degraded" if r.degraded else "up"

        if r.degraded and site.current_state != "down":
            if site.degraded_streak == 0:
                site.degraded_since = checked_at
            site.degraded_streak += 1
            if site.degraded_streak == cfg.degraded_alert_after:
                subject, body = compose("response_degraded", site, [
                    f"The last {site.degraded_streak} checks were slower than {site.degraded_threshold_ms} ms.",
                    f"Latest response time: {r.response_ms} ms.",
                    f"Slow since: {site.degraded_since:%Y-%m-%d %H:%M UTC}.",
                ])
                _push(out, queue_notification(db, event_type="response_degraded", website=site,
                                              dedup_key=f"degraded:{site.id}:{site.degraded_since.isoformat()}",
                                              subject=subject, body=body))
        else:
            site.degraded_streak = 0
            site.degraded_since = None
    else:
        site.consecutive_successes = 0
        site.degraded_streak = 0
        site.degraded_since = None
        if was_down:
            inc = _open_incident(db, site)
            if inc is not None:
                inc.last_check_id = check.id
        else:
            if site.consecutive_failures == 0:
                site.failure_streak_started_at = checked_at
            site.consecutive_failures += 1
            if site.consecutive_failures > site.retry_attempts:
                _confirm_outage(db, site, check, r, out)
            else:
                next_at = checked_at + timedelta(seconds=cfg.retry_delay_s)

    if r.is_server_error and site.current_state != "down":
        _maybe_server_error_alert(db, site, checked_at, out)

    if manual and site.next_check_at is not None:
        site.next_check_at = min(site.next_check_at, next_at)
    elif site.status == "active":
        site.next_check_at = next_at
    return out


def _push(out: Outcome, log_id: int | None) -> None:
    if log_id is not None:
        out.notifications.append(log_id)


def _open_incident(db: Session, site: Website) -> UptimeIncident | None:
    return db.scalar(select(UptimeIncident).where(UptimeIncident.website_id == site.id,
                                                  UptimeIncident.ended_at.is_(None)))


def _confirm_outage(db: Session, site: Website, check: UptimeCheck, r: CheckResult, out: Outcome) -> None:
    started = site.failure_streak_started_at or check.checked_at
    first_id = db.scalar(select(func.min(UptimeCheck.id)).where(
        UptimeCheck.website_id == site.id, UptimeCheck.checked_at >= started, UptimeCheck.ok.is_(False)))
    inc = _open_incident(db, site)
    if inc is None:  # normally true; guards against a stale state
        inc = UptimeIncident(website_id=site.id, started_at=started, status="open", cause_summary=r.summary()[:500],
                             first_check_id=first_id or check.id, last_check_id=check.id)
        db.add(inc)
        db.flush()
    site.current_state = "down"
    out.opened_incident = inc
    attempts = site.consecutive_failures
    subject, body = compose("website_down", site, [
        f"Outage confirmed after {attempts} consecutive failed check{'s' if attempts != 1 else ''}.",
        f"Cause: {r.summary()}" + (f" — {r.error_detail}" if r.error_detail and r.error_type != "http_status" else ""),
        f"First failure: {started:%Y-%m-%d %H:%M:%S UTC}.",
        "You will receive one recovery notice when the site responds normally again.",
    ])
    _push(out, queue_notification(db, event_type="website_down", website=site, dedup_key=f"incident:{inc.id}:down",
                                  subject=subject, body=body))


def _close_incident(db: Session, site: Website, check: UptimeCheck, at: datetime, out: Outcome) -> None:
    inc = _open_incident(db, site)
    if inc is None:
        return
    inc.ended_at = at
    inc.duration_s = int((at - inc.started_at).total_seconds())
    inc.status = "resolved"
    inc.last_check_id = check.id
    out.closed_incident = inc
    subject, body = compose("website_recovered", site, [
        "The website is responding normally again (confirmed by a successful check).",
        f"Outage started: {inc.started_at:%Y-%m-%d %H:%M:%S UTC}",
        f"Recovered:      {at:%Y-%m-%d %H:%M:%S UTC}",
        f"Duration:       {_fmt_duration(inc.duration_s)}",
        f"Original cause: {inc.cause_summary}",
    ])
    _push(out, queue_notification(db, event_type="website_recovered", website=site,
                                  dedup_key=f"incident:{inc.id}:recovered", subject=subject, body=body))


def _maybe_server_error_alert(db: Session, site: Website, at: datetime, out: Outcome) -> None:
    cfg = get_settings()
    window_start = at - timedelta(minutes=cfg.server_error_window_minutes)
    n = db.scalar(select(func.count()).select_from(UptimeCheck).where(
        UptimeCheck.website_id == site.id, UptimeCheck.checked_at > window_start,
        UptimeCheck.http_status >= 500))
    if n and n >= cfg.server_error_alert_threshold:
        bucket = int(at.timestamp() // (cfg.server_error_window_minutes * 60))
        subject, body = compose("repeated_server_errors", site, [
            f"{n} checks returned HTTP 5xx server errors in the last {cfg.server_error_window_minutes} minutes,",
            "although no outage has been confirmed. This often indicates an unstable application or server.",
        ])
        _push(out, queue_notification(db, event_type="repeated_server_errors", website=site,
                                      dedup_key=f"5xx:{site.id}:{bucket}", subject=subject, body=body))
