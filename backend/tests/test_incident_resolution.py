"""Resolving incidents by hand and choosing whether they count in uptime figures and reports."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import UptimeIncident, Website
from app.monitoring.engine import apply_result
from app.monitoring.stats import uptime_for_sites
from app.reports.data import build_snapshot
from tests.test_engine import DOWN, OK


def _outage(db, site, start):
    """Drive the real state machine into a confirmed outage starting at `start`."""
    t = start
    for _ in range(site.retry_attempts + 1):
        apply_result(db, site, DOWN, t)
        t += timedelta(seconds=30)
    db.commit()
    return db.scalar(select(UptimeIncident).where(UptimeIncident.website_id == site.id, UptimeIncident.ended_at.is_(None)))


def test_resolve_closes_incident_and_resets_state(db, make_client, make_website, as_user):
    site = make_website(make_client(), "Site", "https://s.example.net/", retry_attempts=1)
    inc = _outage(db, site, datetime.now(UTC) - timedelta(hours=3))
    assert inc and db.get(Website, site.id).current_state == "down"
    dev = as_user("developer")
    site.assigned_users = [dev.user]
    db.commit()
    r = dev.post(f"/api/v1/incidents/{inc.id}/resolve", json={"note": "Firewall whitelisted the monitor"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "resolved" and body["resolution"] == "manual" and body["include_in_report"] is True
    assert body["resolution_note"] == "Firewall whitelisted the monitor" and body["resolved_by_email"] == dev.user.email
    db.expire_all()
    s = db.get(Website, site.id)
    assert s.current_state == "unknown" and s.consecutive_failures == 0 and s.status == "active"
    assert dev.post(f"/api/v1/incidents/{inc.id}/resolve", json={}).status_code == 409
    # still failing -> the normal retries run again and a NEW incident is opened
    new = _outage(db, s, datetime.now(UTC))
    assert new is not None and new.id != inc.id
    # developers cannot exclude incidents from reports
    assert dev.post(f"/api/v1/incidents/{new.id}/resolve", json={"include_in_report": False}).status_code == 403
    assert dev.put(f"/api/v1/incidents/{new.id}/report", json={"include_in_report": False}).status_code == 403


def test_resolve_and_pause(db, make_client, make_website, as_user):
    site = make_website(make_client(), "Gone", "https://gone.example.net/", retry_attempts=0)
    inc = _outage(db, site, datetime.now(UTC) - timedelta(days=10))
    r = as_user("super_admin").post(f"/api/v1/incidents/{inc.id}/resolve", json={"pause_monitoring": True})
    assert r.status_code == 200 and r.json()["website_status"] == "paused"


def test_excluded_incidents_do_not_count_as_downtime_or_appear_in_reports(db, make_client, make_website, as_user):
    acme = make_client()
    acme.show_expiry_to_client = False
    site = make_website(acme, "Site", "https://s2.example.net/", retry_attempts=0, check_interval_s=60)
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(hours=2)
    t = start  # one hour of checks, the last 30 minutes failing
    while t < start + timedelta(hours=1):
        apply_result(db, site, OK if t < start + timedelta(minutes=30) else DOWN, t)
        t += timedelta(seconds=60)
    db.commit()
    inc = db.scalar(select(UptimeIncident).where(UptimeIncident.website_id == site.id))
    admin = as_user("super_admin")
    down_pct = uptime_for_sites(db, [site.id], start, start + timedelta(hours=1), now=now)[site.id].uptime_pct
    assert down_pct < 60
    r = admin.post(f"/api/v1/incidents/{inc.id}/resolve", json={"include_in_report": False, "note": "False alarm: WAF"})
    assert r.status_code == 200 and r.json()["include_in_report"] is False
    db.expire_all()
    st = uptime_for_sites(db, [site.id], start, start + timedelta(hours=1), now=now)[site.id]
    assert st.uptime_pct == 100.0 and st.incidents == 0
    period = now.strftime("%Y-%m")
    tech = build_snapshot(db, site, period, "technical", now=now)
    client = build_snapshot(db, site, period, "client", now=now)
    assert tech["uptime"]["incidents"] == [] and tech["uptime"]["excluded_incidents"] == 1
    assert client["uptime"]["incidents"] == [] and "excluded_incidents" not in client["uptime"]
    cu = as_user("client", acme)
    site.assigned_users = [cu.user]
    db.commit()
    assert cu.get("/api/v1/incidents?state=all").json() == []
    # the administrator changes their mind
    assert admin.put(f"/api/v1/incidents/{inc.id}/report", json={"include_in_report": True}).json()["include_in_report"] is True
    db.expire_all()
    assert len(build_snapshot(db, site, period, "client", now=now)["uptime"]["incidents"]) == 1
    assert len(cu.get("/api/v1/incidents?state=all").json()) == 1


def test_automatic_recovery_is_marked_auto(db, make_client, make_website):
    site = make_website(make_client(), "Site", "https://s3.example.net/", retry_attempts=0)
    inc = _outage(db, site, datetime.now(UTC) - timedelta(minutes=10))
    apply_result(db, site, OK, datetime.now(UTC))
    db.commit()
    db.refresh(inc)
    assert inc.resolution == "auto" and inc.ended_at is not None
