"""Outage detection state machine: retries, incidents, alerts, recovery, de-duplication."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import NotificationLog, NotificationRule, UptimeCheck, UptimeIncident, Website
from app.monitoring.checker import CheckResult
from app.monitoring.engine import apply_result
from app.notify.senders import MemoryNotifier
from app.worker.tasks import send_notification

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
OK = CheckResult(ok=True, http_status=200, response_ms=120)
SLOW = CheckResult(ok=True, http_status=200, response_ms=5000, degraded=True)
DOWN = CheckResult(ok=False, http_status=503, response_ms=40, error_type="http_status", error_detail="Expected 200; got 503")
TIMEOUT = CheckResult(ok=False, error_type="timeout", error_detail="No response within 15s")


@pytest.fixture
def site(db, make_client, make_website):
    return make_website(make_client("Acme"), "Shop", "https://shop.example.com", retry_attempts=2,
                        recovery_threshold=1, notification_recipients=["ops@acme.example.com"],
                        next_check_at=T0, current_state="unknown")


@pytest.fixture
def step(db, site):
    def _step(result: CheckResult, minutes: float, manual=False):
        s = db.get(Website, site.id, populate_existing=True)
        out = apply_result(db, s, result, T0 + timedelta(minutes=minutes), manual=manual)
        db.commit()
        for log_id in out.notifications:
            send_notification.delay(log_id)
        db.refresh(s)
        return s, out
    return _step


def subjects():
    return [m["subject"] for m in MemoryNotifier.outbox]


def test_healthy_check_marks_up_and_schedules_next(step, site):
    s, out = step(OK, 0)
    assert s.current_state == "up" and out.check.ok and not out.check.is_retry
    assert s.next_check_at == T0 + timedelta(seconds=s.check_interval_s)
    assert MemoryNotifier.outbox == []


def test_failure_is_retried_before_outage_is_confirmed(step, db, settings):
    step(OK, 0)
    s, out = step(DOWN, 5)
    assert s.current_state == "up" and s.consecutive_failures == 1
    assert s.next_check_at == T0 + timedelta(minutes=5, seconds=settings.retry_delay_s)  # quick retry
    s, out = step(DOWN, 5.5)
    assert out.check.is_retry and out.check.attempt == 2 and s.current_state == "up"
    assert db.scalar(select(UptimeIncident)) is None and MemoryNotifier.outbox == []

    s, out = step(DOWN, 6)  # 3rd failure > retry_attempts(2) -> confirmed
    assert s.current_state == "down" and out.opened_incident is not None
    inc = db.scalar(select(UptimeIncident))
    assert inc.started_at == T0 + timedelta(minutes=5)  # starts at the FIRST failure
    assert inc.cause_summary == "Unexpected HTTP 503"
    assert s.next_check_at == T0 + timedelta(minutes=6, seconds=s.check_interval_s)
    assert subjects() == ["[SiteWatch] Website down: Shop"]
    assert "3 consecutive failed checks" in MemoryNotifier.outbox[0]["body"]
    assert MemoryNotifier.outbox[0]["to"] == ["ops@acme.example.com"]


def test_transient_failure_does_not_alert(step, db):
    step(DOWN, 0)
    s, _ = step(OK, 0.5)
    assert s.current_state == "up" and s.consecutive_failures == 0
    assert db.scalar(select(UptimeIncident)) is None and MemoryNotifier.outbox == []


def test_ongoing_outage_alerts_only_once(step, db):
    for i in range(10):
        step(TIMEOUT, i)
    assert db.scalar(select(UptimeIncident).where(UptimeIncident.ended_at.is_(None))) is not None
    assert len(db.scalars(select(UptimeIncident)).all()) == 1
    assert subjects() == ["[SiteWatch] Website down: Shop"]


def test_recovery_closes_incident_and_notifies_once(step, db):
    for i in range(3):
        step(DOWN, i)
    s, out = step(OK, 12)
    inc = db.scalar(select(UptimeIncident))
    assert s.current_state == "up" and inc.ended_at == T0 + timedelta(minutes=12)
    assert inc.duration_s == 12 * 60 and inc.status == "resolved"
    assert subjects() == ["[SiteWatch] Website down: Shop", "[SiteWatch] Website recovered: Shop"]
    assert "Duration:       12m 0s" in MemoryNotifier.outbox[1]["body"]
    step(OK, 17)
    assert len(MemoryNotifier.outbox) == 2


def test_recovery_threshold(step, db, site):
    site.recovery_threshold = 3
    db.commit()
    for i in range(3):
        step(DOWN, i)
    for i, expect in enumerate(["down", "down", "up"]):
        s, _ = step(OK, 10 + i)
        assert s.current_state == expect
    # a failure between successes resets the count
    for i in range(3):
        step(DOWN, 20 + i)
    step(OK, 30)
    step(DOWN, 31)
    s, _ = step(OK, 32)
    assert s.current_state == "down"


def test_second_outage_creates_new_incident_and_alert(step, db):
    for i in range(3):
        step(DOWN, i)
    step(OK, 5)
    for i in range(3):
        step(TIMEOUT, 10 + i)
    incs = db.scalars(select(UptimeIncident).order_by(UptimeIncident.started_at)).all()
    assert len(incs) == 2 and incs[1].cause_summary == "Timed out"
    assert subjects().count("[SiteWatch] Website down: Shop") == 2


def test_zero_retries_confirms_immediately(step, db, site):
    site.retry_attempts = 0
    db.commit()
    s, out = step(DOWN, 0)
    assert s.current_state == "down" and out.opened_incident is not None


def test_degradation_alert_once_per_episode(step, settings):
    for i in range(settings.degraded_alert_after + 3):
        s, _ = step(SLOW, i)
        assert s.current_state == "degraded"
    assert subjects() == ["[SiteWatch] Slow response times: Shop"]
    step(OK, 20)
    for i in range(settings.degraded_alert_after):
        step(SLOW, 30 + i)
    assert subjects().count("[SiteWatch] Slow response times: Shop") == 2


def test_repeated_server_errors_without_confirmed_outage(step, db, site, settings):
    site.retry_attempts = 5  # never confirmed
    db.commit()
    for i in range(settings.server_error_alert_threshold):
        step(DOWN, i * 2)
        step(OK, i * 2 + 1)
    assert "[SiteWatch] Repeated server errors: Shop" in subjects()
    assert db.scalar(select(UptimeIncident)) is None
    step(DOWN, 7)  # same window -> no second alert
    assert subjects().count("[SiteWatch] Repeated server errors: Shop") == 1


def test_no_recipients_is_logged_not_sent(step, db, site):
    site.notification_recipients = []
    db.commit()
    for i in range(3):
        step(DOWN, i)
    log = db.scalar(select(NotificationLog))
    assert log.status == "suppressed" and log.error == "No recipients configured"
    assert MemoryNotifier.outbox == []


def test_rules_add_recipients_and_can_mute(step, db, site):
    db.add_all([
        NotificationRule(event_type="website_down", recipients=["oncall@agency.example.com"]),  # global
        NotificationRule(event_type="website_down", client_id=site.client_id, recipients=["pm@agency.example.com"]),
        NotificationRule(event_type="website_recovered", website_id=site.id, enabled=False),  # mute site recipients
    ])
    db.commit()
    for i in range(3):
        step(DOWN, i)
    assert sorted(MemoryNotifier.outbox[0]["to"]) == ["oncall@agency.example.com", "ops@acme.example.com",
                                                      "pm@agency.example.com"]
    step(OK, 5)
    rec = db.scalar(select(NotificationLog).where(NotificationLog.event_type == "website_recovered"))
    assert rec.status == "suppressed"


def test_failed_delivery_is_recorded(step, db):
    MemoryNotifier.fail_next = 1
    for i in range(3):
        step(DOWN, i)
    log = db.scalar(select(NotificationLog))
    assert log.status == "failed" and "simulated" in log.error


def test_paused_site_manual_check_does_not_reschedule(step, db, site):
    site.status = "paused"
    site.next_check_at = None
    db.commit()
    s, out = step(OK, 0, manual=True)
    assert out.check.is_manual and s.next_check_at is None


def test_checks_are_recorded_with_details(step, db):
    step(TIMEOUT, 0)
    c = db.scalar(select(UptimeCheck))
    assert (c.ok, c.error_type, c.error_detail) == (False, "timeout", "No response within 15s")
