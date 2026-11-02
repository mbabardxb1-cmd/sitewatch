import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PerformanceMetric, UptimeCheck, UptimeIncident, Website
from app.monitoring.stats import aggregate, response_series, rollup, uptime_for_sites
from app.worker import tasks

NOW = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)


def add_checks(db, site, start, minutes, every=5, ok=True, ms=100):
    for i in range(0, minutes, every):
        db.add(UptimeCheck(website_id=site.id, checked_at=start + timedelta(minutes=i), ok=ok, http_status=200,
                           response_ms=ms))
    db.commit()


def test_no_checks_means_no_uptime_value(db, make_client, make_website):
    site = make_website(make_client(), check_interval_s=300)
    st = uptime_for_sites(db, [site.id], NOW - timedelta(days=1), NOW, now=NOW)[site.id]
    assert st.uptime_pct is None and st.monitored_s == 0 and st.unknown_s == 86400


def test_full_coverage_with_one_incident(db, make_client, make_website):
    site = make_website(make_client(), check_interval_s=300)
    start = NOW - timedelta(days=1)
    add_checks(db, site, start, 24 * 60)
    db.add(UptimeIncident(website_id=site.id, started_at=start + timedelta(hours=2),
                          ended_at=start + timedelta(hours=2, minutes=36), duration_s=36 * 60, status="resolved"))
    db.commit()
    st = uptime_for_sites(db, [site.id], start, NOW, now=NOW)[site.id]
    assert st.monitored_s == 86400 and st.downtime_s == 36 * 60 and st.incidents == 1
    assert st.uptime_pct == round((86400 - 2160) / 86400 * 100, 3)  # 97.5
    assert st.coverage_pct == 100.0 and st.checks == 288


def test_gaps_are_unknown_not_uptime(db, make_client, make_website):
    """Monitoring stopped for 12 hours: that time must not be counted as up."""
    site = make_website(make_client(), check_interval_s=300)
    start = NOW - timedelta(days=1)
    add_checks(db, site, start, 6 * 60)                          # 00:00-06:00
    add_checks(db, site, start + timedelta(hours=18), 6 * 60)    # 18:00-24:00
    st = uptime_for_sites(db, [site.id], start, NOW, now=NOW)[site.id]
    # Block 1: 71 checks x 5 min + the last check's capped tail (interval x 2.5 = 12.5 min).
    # Block 2: 72 checks x 5 min, the last one ending at "now".
    assert st.uptime_pct == 100.0
    assert st.monitored_s == (71 * 5 + 12.5) * 60 + 72 * 5 * 60
    assert st.unknown_s > 11 * 3600 and st.coverage_pct < 55


def test_open_incident_counts_until_now(db, make_client, make_website):
    site = make_website(make_client(), check_interval_s=300)
    start = NOW - timedelta(hours=1)
    add_checks(db, site, start, 60)
    db.add(UptimeIncident(website_id=site.id, started_at=NOW - timedelta(minutes=30), status="open"))
    db.commit()
    st = uptime_for_sites(db, [site.id], start, NOW, now=NOW)[site.id]
    assert st.downtime_s == 1800 and st.uptime_pct == 50.0


def test_incident_clipped_to_period(db, make_client, make_website):
    site = make_website(make_client(), check_interval_s=300)
    start = NOW - timedelta(hours=1)
    add_checks(db, site, start - timedelta(hours=1), 120)
    db.add(UptimeIncident(website_id=site.id, started_at=NOW - timedelta(minutes=90),
                          ended_at=NOW - timedelta(minutes=45), duration_s=2700, status="resolved"))
    db.commit()
    st = uptime_for_sites(db, [site.id], start, NOW, now=NOW)[site.id]
    assert st.downtime_s == 15 * 60


def test_aggregate_weights_by_monitored_time(db):
    from app.monitoring.stats import UptimeStats
    a = UptimeStats(NOW, NOW, 90.0, 1000, 100, 0, 100, 1, 1)
    b = UptimeStats(NOW, NOW, 100.0, 9000, 0, 0, 100, 1, 0)
    c = UptimeStats(NOW, NOW, None, 0, 0, 0, 0, 0, 0)
    assert aggregate([a, b, c]) == 99.0
    assert aggregate([c]) is None


def test_response_series_and_rollup(db, make_client, make_website):
    site = make_website(make_client(), check_interval_s=300)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    for ms in (100, 200, 300, 400):
        db.add(UptimeCheck(website_id=site.id, checked_at=hour + timedelta(minutes=ms // 10), ok=True,
                           http_status=200, response_ms=ms))
    db.add(UptimeCheck(website_id=site.id, checked_at=hour + timedelta(minutes=50), ok=False, error_type="timeout"))
    db.commit()
    series = response_series(db, site.id, 24)
    assert len(series) == 1
    b = series[0]
    assert (b["checks"], b["successes"], b["avg_ms"], b["max_ms"]) == (5, 4, 250, 400)
    rollup(db, hours_back=4)
    db.commit()
    m = db.scalar(select(PerformanceMetric))
    assert (m.checks, m.successes, m.avg_ms) == (5, 4, 250)
    rollup(db, hours_back=4)  # idempotent upsert
    db.commit()
    assert len(db.scalars(select(PerformanceMetric)).all()) == 1
    # raw checks purged -> series still available from rollups
    db.query(UptimeCheck).delete()
    db.commit()
    assert response_series(db, site.id, 24)[0]["avg_ms"] == 250


# ---- Scheduler --------------------------------------------------------------------------

def test_dispatcher_claims_only_due_active_sites(db, make_client, make_website):
    acme, gone = make_client("Acme"), make_client("Gone")
    now = datetime.now(UTC)
    due = make_website(acme, "Due", "https://due.example.com", next_check_at=now - timedelta(seconds=1))
    new = make_website(acme, "New", "https://new.example.com", next_check_at=None)
    make_website(acme, "Later", "https://later.example.com", next_check_at=now + timedelta(minutes=3))
    make_website(acme, "Paused", "https://p.example.com", status="paused", next_check_at=now)
    make_website(acme, "Archived", "https://a.example.com", status="archived", next_check_at=now)
    inactive = make_website(gone, "Inactive client", "https://i.example.com", next_check_at=now)
    gone.is_active = False
    db.commit()
    claimed = tasks.claim_due_websites(db, now=now)
    assert set(claimed) == {due.id, new.id}
    assert inactive.id not in claimed
    # lease pushed forward: a second pass claims nothing
    assert tasks.claim_due_websites(db, now=now) == []
    db.expire_all()
    assert db.get(Website, due.id).next_check_at == now + timedelta(seconds=300)


def test_concurrent_dispatchers_never_double_claim(db, make_client, make_website):
    acme = make_client("Acme")
    now = datetime.now(UTC)
    ids = {make_website(acme, f"S{i}", f"https://s{i}.example.com", next_check_at=now).id for i in range(40)}
    results: list[list] = []
    barrier = threading.Barrier(4)

    def worker():
        with SessionLocal() as s:
            barrier.wait()
            results.append(tasks.claim_due_websites(s, limit=15, now=now))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    claimed = [i for r in results for i in r]
    assert len(claimed) == len(set(claimed)) == 40 and set(claimed) == ids


def test_full_pipeline_dispatch_check_alert(db, make_client, make_website, settings):
    """Beat tick -> dispatcher -> check task (eager) -> state machine -> email, against a real server."""
    from app.notify.senders import MemoryNotifier
    from tests.httpfixtures import LocalSite

    settings.private_target_allowlist = ["127.0.0.1/32"]
    with LocalSite() as srv:
        site = make_website(make_client(), "Local", srv.base + "/", retry_attempts=0,
                            notification_recipients=["ops@example.com"], next_check_at=None)
        assert tasks.dispatch_due_checks() == 1
        db.expire_all()
        assert db.get(Website, site.id).current_state == "up"
        srv.state["mode"] = "500"
        db.get(Website, site.id).next_check_at = None
        db.commit()
        tasks.dispatch_due_checks()
        db.expire_all()
        assert db.get(Website, site.id).current_state == "down"
    assert [m["subject"] for m in MemoryNotifier.outbox] == ["[SiteWatch] Website down: Local"]
    checks = db.scalars(select(UptimeCheck).order_by(UptimeCheck.id)).all()
    assert [(c.ok, c.http_status) for c in checks] == [(True, 200), (False, 500)]
