"""Role-aware dashboard notices, open-issue counts and latest reports."""
from datetime import UTC, datetime, timedelta

import pytest

from app.models import DomainRecord, HostingRecord, Issue, MonthlyReport, SslCertificate, UptimeIncident


@pytest.fixture
def world(db, make_client, make_website, as_user):
    acme, other = make_client("Acme", show_expiry=False), make_client("Other", show_expiry=True)
    admin, dev = as_user("super_admin"), as_user("developer")
    cu, cu_other = as_user("client", acme), as_user("client", other)
    site = make_website(acme, "Shop", "https://shop.example.com/", assign=[cu.user, dev.user])
    make_website(other, "Elsewhere", "https://else.example.com/", assign=[cu_other.user])
    now = datetime.now(UTC)
    db.add_all([
        UptimeIncident(website_id=site.id, started_at=now - timedelta(hours=2), status="open",
                       cause_summary="Unexpected HTTP 503"),
        Issue(website_id=site.id, fingerprint="a", check_key="missing_title", title="t", severity="high"),
        Issue(website_id=site.id, fingerprint="b", check_key="robots_txt", title="i", severity="info"),
        DomainRecord(website_id=site.id, domain="example.com", status="unknown", nameservers=[],
                     manual_expires_at=now + timedelta(days=10), manual_entered_at=now),
        HostingRecord(website_id=site.id, renewal_date=(now + timedelta(days=200)).date(), source="manual"),
        SslCertificate(website_id=site.id, hostname="shop.example.com", valid=False, validation_error="expired",
                       not_after=now - timedelta(days=1), checked_at=now, san=[]),
        MonthlyReport(website_id=site.id, period="2026-10", kind="client", status="ready", approved_for_client=True,
                      approved_at=now - timedelta(days=1), data_snapshot={"status": {"level": "warning"}}),
        MonthlyReport(website_id=site.id, period="2026-09", kind="client", status="ready", approved_for_client=False),
    ])
    db.commit()
    return dict(admin=admin, dev=dev, cu=cu, cu_other=cu_other, site=site, acme=acme)


def texts(c):
    return [n["text"] for n in c.get("/api/v1/dashboard/summary").json()["notices"]]


def test_staff_notices(world):
    t = texts(world["dev"])
    assert t[0].startswith("Shop is unavailable since") and "(Unexpected HTTP 503)" in t[0]
    assert "Shop: SSL certificate is not valid" in t
    assert "Shop: Domain expires in 10 days (manually entered date)" in t
    assert "Shop: 1 high-priority issue outstanding" in t
    assert not any("awaiting publication" in x for x in t)  # developers cannot publish
    assert "1 client report awaiting publication" in texts(world["admin"])


def test_client_notices_are_plain_and_respect_expiry_setting(world, db):
    t = texts(world["cu"])
    assert t[0].startswith("Shop is unavailable since") and "503" not in t[0]
    assert not any("Domain" in x or "SSL" in x for x in t)  # expiry not shared with this client
    assert "Shop: the October 2026 report is available" in t
    world["acme"].show_expiry_to_client = True
    db.commit()
    t = texts(world["cu"])
    assert "Shop: Domain expires in 10 days" in t and "Shop: SSL certificate is not valid" in t


def test_counts_and_latest_reports_are_role_shaped(world):
    s = world["cu"].get("/api/v1/dashboard/summary").json()
    assert s["open_issues"][str(world["site"].id)] == {"high": 1}  # info hidden from clients
    assert [(r["period"], r["published"]) for r in s["latest_reports"]] == [("2026-10", True)]
    staff = world["dev"].get("/api/v1/dashboard/summary").json()
    assert staff["open_issues"][str(world["site"].id)] == {"high": 1, "info": 1}


def test_other_tenant_sees_nothing_of_acme(world):
    s = world["cu_other"].get("/api/v1/dashboard/summary").json()
    assert not any("Shop" in n["text"] for n in s["notices"])
    assert s["latest_reports"] == [] and str(world["site"].id) not in s["open_issues"]


def test_stale_monitoring_is_not_presented_as_current(world, db):
    site = world["site"]
    now = datetime.now(UTC)
    site.current_state, site.last_checked_at, site.check_interval_s = "up", now - timedelta(hours=3), 300
    db.query(UptimeIncident).delete()
    db.commit()
    w = world["cu"].get(f"/api/v1/websites/{site.id}").json()
    assert w["health"] == "unknown" and w["health_reason"].startswith("No recent checks (last check 3 h ago)")
    summary = world["cu"].get("/api/v1/dashboard/summary").json()
    assert (summary["online"], summary["unknown"]) == (0, 1)  # tiles agree with the status label
    assert "Uptime checks are overdue for 1 website" in " ".join(texts(world["admin"]))
    assert not any("overdue" in x for x in texts(world["cu"]))  # operational detail is for staff
    site.current_state = "down"
    db.commit()
    w = world["cu"].get(f"/api/v1/websites/{site.id}").json()
    assert w["health"] == "critical" and "no checks since" in w["health_reason"]  # an outage is never hidden
    site.last_checked_at = now - timedelta(minutes=5)
    site.current_state = "up"
    db.commit()
    assert world["cu"].get(f"/api/v1/websites/{site.id}").json()["health"] == "healthy"


def test_audit_state_distinguishes_never_and_unreachable(world, db):
    from app.models import AuditRun

    sid = str(world["site"].id)
    s = world["cu"].get("/api/v1/dashboard/summary").json()
    assert s["audit_state"][sid] == {"last_audit_at": None, "reached_site": None}
    db.add(AuditRun(website_id=world["site"].id, trigger="manual", status="partial", pages_crawled=0,
                    finished_at=datetime.now(UTC)))
    db.commit()
    s = world["cu"].get("/api/v1/dashboard/summary").json()
    assert s["audit_state"][sid]["reached_site"] is False
