import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import NotificationLog, UptimeCheck, UptimeIncident
from app.notify.senders import MemoryNotifier
from tests.httpfixtures import LocalSite


@pytest.fixture
def world(make_client, make_website, as_user, db):
    acme, globex = make_client("Acme"), make_client("Globex")
    dev, admin = as_user("developer"), as_user("super_admin")
    acme_cu = as_user("client", acme)
    a1 = make_website(acme, "A1", "https://a1.example.com", assign=[dev.user, acme_cu.user])
    g1 = make_website(globex, "G1", "https://g1.example.com")
    now = datetime.now(UTC)
    for site in (a1, g1):
        db.add(UptimeCheck(website_id=site.id, checked_at=now - timedelta(minutes=10), ok=False,
                           http_status=500, error_type="http_status", error_detail="internal stack trace here"))
        db.add(UptimeCheck(website_id=site.id, checked_at=now - timedelta(minutes=5), ok=True, http_status=200,
                           response_ms=150))
    inc_a = UptimeIncident(website_id=a1.id, started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=5),
                           duration_s=300, status="resolved", cause_summary="Unexpected HTTP 500")
    inc_g = UptimeIncident(website_id=g1.id, started_at=now - timedelta(minutes=10), status="open",
                           cause_summary="Timed out")
    db.add_all([inc_a, inc_g])
    db.commit()
    return dict(dev=dev, admin=admin, acme_cu=acme_cu, a1=a1, g1=g1, inc_a=inc_a, inc_g=inc_g)


def test_uptime_summary_shape_and_scope(world):
    w = world
    r = w["acme_cu"].get(f"/api/v1/websites/{w['a1'].id}/uptime")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"day", "week", "month", "calendar_month", "method"}
    assert body["day"]["checks"] == 2 and body["day"]["incidents"] == 1
    assert w["acme_cu"].get(f"/api/v1/websites/{w['g1'].id}/uptime").status_code == 404
    assert w["dev"].get(f"/api/v1/websites/{w['g1'].id}/response-times").status_code == 404


def test_raw_checks_are_staff_only(world):
    w = world
    assert w["acme_cu"].get(f"/api/v1/websites/{w['a1'].id}/checks").status_code == 403
    rows = w["dev"].get(f"/api/v1/websites/{w['a1'].id}/checks").json()
    assert [c["http_status"] for c in rows] == [200, 500]
    assert rows[1]["error_detail"] == "internal stack trace here"
    fails = w["dev"].get(f"/api/v1/websites/{w['a1'].id}/checks?only_failures=true").json()
    assert len(fails) == 1
    assert w["dev"].get(f"/api/v1/websites/{w['g1'].id}/checks").status_code == 404


def test_incidents_scoped_and_shaped(world):
    w = world
    client_list = w["acme_cu"].get("/api/v1/incidents").json()
    assert [i["id"] for i in client_list] == [str(w["inc_a"].id)]
    assert "cause_summary" not in client_list[0] and "first_check_id" not in client_list[0]
    staff = w["dev"].get("/api/v1/incidents").json()
    assert [i["cause_summary"] for i in staff] == ["Unexpected HTTP 500"]
    admin_open = w["admin"].get("/api/v1/incidents?state=open").json()
    assert [i["id"] for i in admin_open] == [str(w["inc_g"].id)]
    assert w["acme_cu"].get(f"/api/v1/incidents/{w['inc_g'].id}").status_code == 404
    assert w["acme_cu"].get(f"/api/v1/incidents/{uuid.uuid4()}").status_code == 404


def test_acknowledge_incident(world):
    w = world
    assert w["acme_cu"].post(f"/api/v1/incidents/{w['inc_a'].id}/acknowledge").status_code == 403
    assert w["dev"].post(f"/api/v1/incidents/{w['inc_g'].id}/acknowledge").status_code == 404  # not assigned
    assert w["admin"].post(f"/api/v1/incidents/{w['inc_a'].id}/acknowledge").status_code == 409  # resolved
    r = w["admin"].post(f"/api/v1/incidents/{w['inc_g'].id}/acknowledge")
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    assert r.json()["acknowledged_by_email"] == w["admin"].user.email


def test_dashboard_reports_real_uptime(world):
    s = world["dev"].get("/api/v1/dashboard/summary").json()
    assert s["monthly_uptime_pct"] is not None and 0 <= s["monthly_uptime_pct"] <= 100
    assert s["uptime_30d"][str(world["a1"].id)] == s["monthly_uptime_pct"]
    assert s["active_incidents"] == 0  # the open incident belongs to an unassigned site


def test_check_now_runs_check_and_is_rate_limited(as_user, make_client, make_website, settings, db):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    dev = as_user("developer")
    with LocalSite() as srv:
        site = make_website(make_client(), "Local", srv.base + "/", assign=[dev.user])
        r = dev.post(f"/api/v1/websites/{site.id}/check-now")
        assert r.status_code == 202
        assert dev.post(f"/api/v1/websites/{site.id}/check-now").status_code == 429
    check = db.scalar(select(UptimeCheck).where(UptimeCheck.website_id == site.id))
    assert check.ok and check.is_manual and check.http_status == 200


def test_check_now_permissions(world):
    w = world
    assert w["acme_cu"].post(f"/api/v1/websites/{w['a1'].id}/check-now").status_code == 403
    assert w["dev"].post(f"/api/v1/websites/{w['g1'].id}/check-now").status_code == 404


# ---- Notification rules & logs ----------------------------------------------------------

def test_rules_admin_only_and_validated(world):
    w = world
    payload = {"event_type": "website_down", "recipients": ["oncall@agency.example.com"], "min_interval_s": 600}
    assert w["dev"].post("/api/v1/notification-rules", json=payload).status_code == 403
    assert w["dev"].get("/api/v1/notification-rules").status_code == 403
    assert w["acme_cu"].get("/api/v1/notification-logs").status_code == 403
    assert w["admin"].post("/api/v1/notification-rules", json={**payload, "event_type": "nope"}).status_code == 422
    assert w["admin"].post("/api/v1/notification-rules", json={**payload, "recipients": ["bad"]}).status_code == 422
    assert w["admin"].post("/api/v1/notification-rules", json={
        **payload, "website_id": str(w["a1"].id), "client_id": str(w["a1"].client_id)}).status_code == 422
    r = w["admin"].post("/api/v1/notification-rules", json=payload)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert w["admin"].patch(f"/api/v1/notification-rules/{rid}", json={"enabled": False}).json()["enabled"] is False
    assert w["admin"].delete(f"/api/v1/notification-rules/{rid}").status_code == 204
    events = w["dev"].get("/api/v1/notification-events").json()
    assert {"key": "ssl_expiry", "label": "SSL certificate expiry approaching"} in events


def test_logs_scoped_for_developers(world, db):
    w = world
    for site in (w["a1"], w["g1"]):
        db.add(NotificationLog(website_id=site.id, event_type="website_down", dedup_key=f"k{site.id}",
                               channel="email", recipients=["x@example.com"], subject=site.name, status="sent"))
    db.commit()
    assert [x["subject"] for x in w["dev"].get("/api/v1/notification-logs").json()] == ["A1"]
    assert len(w["admin"].get("/api/v1/notification-logs").json()) == 2


def test_test_email_with_memory_backend(world):
    r = world["admin"].post("/api/v1/notifications/test", json={"recipient": "me@example.com"})
    assert r.json() == {"ok": True, "error": None}
    assert MemoryNotifier.outbox[-1]["to"] == ["me@example.com"]


def test_smtp_notifier_delivers_real_email(settings):
    """End-to-end over SMTP against a local aiosmtpd server."""
    from aiosmtpd.controller import Controller

    from app.notify.senders import SmtpNotifier

    received = []

    class H:
        async def handle_DATA(self, server, session, envelope):  # noqa: N802
            received.append(envelope)
            return "250 OK"

    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ctrl = Controller(H(), hostname="127.0.0.1", port=port)
    ctrl.start()
    try:
        settings.smtp_host, settings.smtp_port = "127.0.0.1", port
        settings.smtp_starttls = False
        settings.smtp_from = "SiteWatch <alerts@agency.example.com>"
        SmtpNotifier().send(["a@example.com", "b@example.com"], "Subject line", "Body text")
        for _ in range(50):
            if received:
                break
            time.sleep(0.05)
    finally:
        ctrl.stop()
    env = received[0]
    assert env.rcpt_tos == ["a@example.com", "b@example.com"]
    assert b"Subject: Subject line" in env.content and b"Body text" in env.content


def test_smtp_not_configured_reports_error(settings):
    from app.notify.senders import NotifierError, SmtpNotifier

    settings.smtp_host = ""
    with pytest.raises(NotifierError, match="not configured"):
        SmtpNotifier().send(["a@example.com"], "s", "b")


def test_uptime_overview_scoped(world):
    rows = world["dev"].get("/api/v1/uptime/overview").json()
    assert [r["website_id"] for r in rows] == [str(world["a1"].id)]
    assert rows[0]["avg_response_ms_24h"] == 150 and rows[0]["day"] is not None
