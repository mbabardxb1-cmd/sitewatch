"""Monthly reports: snapshot facts, PDF content, lifecycle, permissions, scheduling, branding."""
import io
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import insert, select

from app.db import SessionLocal
from app.models import (
    AuditRun,
    DomainRecord,
    HostingRecord,
    Issue,
    MaintenanceActivity,
    MonthlyReport,
    SslCertificate,
    UptimeCheck,
    UptimeIncident,
)
from app.notify.senders import MemoryNotifier
from app.reports import service as rsvc
from app.reports.data import build_snapshot
from app.reports.pdf import render_pdf

PERIOD = "2026-10"
OCT = datetime(2026, 10, 1, tzinfo=UTC)
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.storage_dir = str(tmp_path / "storage")
    yield tmp_path / "storage"


@pytest.fixture
def site(db, make_client, make_website):
    client = make_client("Acme", show_expiry=False)
    w = make_website(client, "Shop <b>&amp;</b>", "https://shop.example.com/", notification_recipients=["ops@acme.example.com"])
    w.created_at = datetime(2026, 9, 1, tzinfo=UTC)
    # checks every 5 minutes (the site's interval) for September and October; 2-hour outage on 10 Oct
    rows = []
    for month_start, days, ms in ((datetime(2026, 9, 1, tzinfo=UTC), 30, 200), (OCT, 31, 300)):
        for k in range(days * 288):
            t = month_start + timedelta(minutes=5 * k)
            down = month_start == OCT and datetime(2026, 10, 10, 2, tzinfo=UTC) <= t < datetime(2026, 10, 10, 4, tzinfo=UTC)
            rows.append({"website_id": w.id, "checked_at": t, "ok": not down, "http_status": 503 if down else 200,
                         "response_ms": None if down else ms + (k % 288) // 12, "degraded": False, "attempt": 1,
                         "is_retry": False, "is_manual": False})
    db.execute(insert(UptimeCheck), rows)
    db.add(UptimeIncident(website_id=w.id, started_at=datetime(2026, 10, 10, 2, tzinfo=UTC),
                          ended_at=datetime(2026, 10, 10, 4, tzinfo=UTC), duration_s=7200, status="resolved",
                          cause_summary="Unexpected HTTP 503"))
    run = AuditRun(website_id=w.id, trigger="scheduled", period=PERIOD, status="completed",
                   finished_at=datetime(2026, 10, 2, tzinfo=UTC), pages_crawled=42,
                   summary={"by_severity": {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 1},
                            "by_certainty": {"confirmed": 6, "warning": 0, "could_not_check": 2},
                            "broken_internal_links": 1, "broken_external_links": 2, "external_links_checked": 30,
                            "counts": {"new": 2, "recurring": 0, "resolved": 1, "persisting": 3}},
                   check_coverage={"broken_internal_link": {"status": "performed"},
                                   "javascript_rendering": {"status": "not_checked", "reason": "JS not rendered"}})
    db.add(run)
    db.flush()
    db.add_all([
        Issue(website_id=w.id, fingerprint="a", check_key="broken_internal_link", title="HTTP 404: /pricing <script>",
              severity="high", status="open", url="https://shop.example.com/pricing", created_at=OCT + timedelta(days=1)),
        Issue(website_id=w.id, fingerprint="b", check_key="missing_title", title="Missing <title>: /about",
              severity="medium", status="resolved", resolved_at=OCT + timedelta(days=20), resolved_verified_run_id=run.id,
              resolution_note="Re-checked by the audit; no longer observed.", created_at=OCT - timedelta(days=40)),
        Issue(website_id=w.id, fingerprint="c", check_key="robots_txt", title="No robots.txt file", severity="info",
              status="open", created_at=OCT + timedelta(days=1)),
        MaintenanceActivity(website_id=w.id, performed_at=OCT + timedelta(days=5), title="Updated CMS core to 6.4",
                            category="update", visible_to_client=True),
        MaintenanceActivity(website_id=w.id, performed_at=OCT + timedelta(days=6), title="Rotated DB password",
                            category="security", visible_to_client=False),
        DomainRecord(website_id=w.id, domain="example.com", source="rdap", status="ok", registrar="Registrar X",
                     expires_at=datetime.now(UTC) + timedelta(days=20), verified_at=datetime.now(UTC), nameservers=[]),
        HostingRecord(website_id=w.id, provider="HostCo", renewal_date=(datetime.now(UTC) + timedelta(days=200)).date(),
                      source="manual", account_reference="ACCT-1", internal_notes="secret note"),
        SslCertificate(website_id=w.id, hostname="shop.example.com", valid=True, issuer="R11",
                       not_after=datetime.now(UTC) + timedelta(days=70), checked_at=datetime.now(UTC), san=[]),
    ])
    db.commit()
    return w


def text_of(pdf: bytes) -> str:
    """All page text with whitespace normalised (table cells wrap onto several lines)."""
    return " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).split())


# ---- snapshot ------------------------------------------------------------------------------

def test_technical_snapshot_facts(db, site):
    s = build_snapshot(db, site, PERIOD, "technical", now=NOW)
    up = s["uptime"]
    assert up["incidents"][0]["cause"] == "Unexpected HTTP 503" and up["downtime_s"] == 7200
    assert up["uptime_pct"] == pytest.approx((31 * 86400 - 7200) / (31 * 86400) * 100, abs=0.01)
    assert up["previous"]["uptime_pct"] == 100.0 and s["period_complete"] is True
    assert s["response"]["avg_ms"] > s["response"]["previous_avg_ms"]  # 300+ vs 200+
    assert len(s["response"]["daily"]) == 31
    assert s["audit"]["pages_crawled"] == 42
    js = next(c for c in s["audit"]["coverage"]["not_checked"] if c["label"] == "JavaScript-rendered content")
    assert js["reason"] == "JS not rendered"
    iss = s["issues"]
    assert [i["title"] for i in iss["resolved"]] == ["Missing <title>: /about"]
    assert iss["resolved"][0]["resolution_note"].startswith("Re-checked")
    assert iss["outstanding_total"] == 2 and iss["new_total"] == 2  # info included for technical
    assert [m["title"] for m in s["maintenance"]] == ["Updated CMS core to 6.4", "Rotated DB password"]
    assert s["expiry"]["domain"]["source"] == "rdap" and s["expiry"]["hosting"]["source"] == "manual"
    assert s["status"]["level"] == "warning"  # 1 high issue, domain in 20 days, uptime < 99.9
    assert any("Domain expires in" in r for r in s["status"]["reasons"])
    texts = [r["text"] for r in s["recommendations"]]
    assert "Renew the domain before it expires." in texts and "Fix the outstanding critical and high-severity issues." in texts
    assert s["recommendations"][0]["priority"] == "high"
    assert any("1 issue(s) were confirmed fixed by a follow-up audit" in x for x in s["summary"])


def test_client_snapshot_omits_internal_details(db, site):
    s = build_snapshot(db, site, PERIOD, "client", now=NOW)
    assert "cause" not in s["uptime"]["incidents"][0]
    assert [m["title"] for m in s["maintenance"]] == ["Updated CMS core to 6.4"]
    assert s["issues"]["outstanding_total"] == 1  # info excluded
    assert "url" not in s["issues"]["outstanding"][0]
    assert s["expiry"] is None  # client has expiry visibility switched off
    site.client.show_expiry_to_client = True
    db.commit()
    s2 = build_snapshot(db, site, PERIOD, "client", now=NOW)
    assert s2["expiry"]["hosting"]["provider"] == "HostCo"
    assert "ACCT-1" not in str(s2) and "secret note" not in str(s2)


def test_period_without_data_is_reported_as_missing(db, make_client, make_website):
    w = make_website(make_client(), "Empty", "https://empty.example.com/")
    s = build_snapshot(db, w, PERIOD, "technical", now=NOW)
    assert s["uptime"]["uptime_pct"] is None and s["audit"] is None and s["status"]["level"] == "unknown"
    assert "cannot be reported" in s["summary"][0]
    assert any("Run a technical audit" in r["text"] for r in s["recommendations"])
    pdf = render_pdf(s)
    txt = text_of(pdf)
    assert "No technical audit was completed during this period" in txt
    assert "No maintenance work was recorded for this period." in txt


# ---- PDF ---------------------------------------------------------------------------------

def test_pdf_content_and_structure(db, site):
    snap = build_snapshot(db, site, PERIOD, "technical", now=NOW, branding={"company_name": "Agency Ltd",
                                                                              "footer_text": "Confidential"})
    pdf = render_pdf(snap)
    assert pdf.startswith(b"%PDF")
    if os.environ.get("SAVE_PDF_DIR"):  # opt-in: keep a sample for visual review
        open(os.path.join(os.environ["SAVE_PDF_DIR"], "sample-full.pdf"), "wb").write(pdf)
    reader = PdfReader(io.BytesIO(pdf))
    n = len(reader.pages)
    txt = text_of(pdf)
    for needle in ("Website maintenance report (technical)", "Shop <b>&amp;</b>", "October 2026",
                   "Overall status: WARNING", "1. Monitoring results", "1.1 Availability", "1.3 Technical audit",
                   "Fixed (verified by a later audit)", "Outstanding issues requiring attention",
                   "HTTP 404: /pricing <script>", "2. Maintenance work performed", "Updated CMS core to 6.4",
                   "3. Recommendations", "4. Comparison with the previous month", "5. Notes on method",
                   "Verified (RDAP)", "Manual entry - not verified", "Average response time per day",
                   "Unexpected HTTP 503", f"Page 1 of {n}", f"Page {n} of {n}", "Agency Ltd", "Confidential"):
        assert needle in txt, needle
    assert reader.metadata.title.startswith("Shop")


def test_client_pdf_has_no_technical_details(db, site):
    txt = text_of(render_pdf(build_snapshot(db, site, PERIOD, "client", now=NOW)))
    assert "Unexpected HTTP 503" not in txt and "Rotated DB password" not in txt and "No robots.txt" not in txt
    assert "Website maintenance report" in txt and "(technical)" not in txt


def test_logo_is_embedded(db, site, as_user):
    admin = as_user("super_admin")
    buf = io.BytesIO()
    Image.new("RGB", (300, 100), (30, 58, 138)).save(buf, format="JPEG")
    r = admin.post("/api/v1/settings/branding/logo", files={"file": ("logo.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200
    snap = build_snapshot(db, site, PERIOD, "client", now=NOW, branding=rsvc.branding(db))
    pdf = render_pdf(snap, rsvc.logo_file(db))
    assert len(PdfReader(io.BytesIO(pdf)).pages[0].images) == 1


# ---- lifecycle via API ------------------------------------------------------------------------

@pytest.fixture
def people(site, as_user, make_user, make_client):
    dev, admin = as_user("developer"), as_user("super_admin")
    cu = as_user("client", site.client)
    other_dev = as_user("developer")
    site.assigned_users = [dev.user, cu.user]
    from sqlalchemy.orm import object_session
    object_session(site).commit()
    return dict(dev=dev, admin=admin, cu=cu, other_dev=other_dev)


def gen(c, site, **kw):
    return c.post(f"/api/v1/websites/{site.id}/reports", json={"period": PERIOD, "kind": "both", **kw})


def test_generate_store_and_regenerate(db, site, people, storage):
    p = people
    r = gen(p["dev"], site)
    assert r.status_code == 202 and {x["kind"] for x in r.json()} == {"client", "technical"}
    reports = db.scalars(select(MonthlyReport)).all()
    assert all(x.status == "ready" and x.size_bytes > 10000 and x.generation_count == 1 for x in reports)
    for x in reports:
        assert (storage / x.file_path).exists() and x.data_snapshot["period"] == PERIOD
    assert gen(p["dev"], site).status_code == 409
    client_report = next(x for x in reports if x.kind == "client")
    p["admin"].post(f"/api/v1/reports/{client_report.id}/approve", json={})
    r = gen(p["dev"], site, regenerate=True, kind="client")
    db.expire_all()
    again = db.get(MonthlyReport, client_report.id)
    assert again.generation_count == 2 and again.approved_for_client is False  # approval withdrawn
    assert len(list((storage / "reports").rglob("*.pdf"))) == 2  # old file replaced, not accumulated
    assert gen(p["dev"], site, period="2099-01").status_code == 422
    assert gen(p["dev"], site, period="2026-13").status_code == 422


def test_report_access_rules(db, site, people):
    p = people
    gen(p["dev"], site)
    ids = {x.kind: x.id for x in db.scalars(select(MonthlyReport))}
    assert p["cu"].get("/api/v1/reports").json() == []
    assert p["cu"].get(f"/api/v1/reports/{ids['client']}/download").status_code == 404  # not approved yet
    assert p["cu"].post(f"/api/v1/reports/{ids['client']}/approve", json={}).status_code == 403
    assert p["dev"].post(f"/api/v1/reports/{ids['client']}/approve", json={}).status_code == 403
    assert p["other_dev"].get(f"/api/v1/reports/{ids['technical']}/download").status_code == 404
    assert p["admin"].post(f"/api/v1/reports/{ids['technical']}/approve", json={}).status_code == 409
    assert p["admin"].post(f"/api/v1/reports/{ids['client']}/approve", json={}).status_code == 200
    listed = p["cu"].get("/api/v1/reports").json()
    assert [x["kind"] for x in listed] == ["client"]
    assert listed[0]["error"] is None and listed[0]["emailed_to"] == []  # staff-only fields stay empty
    dl = p["cu"].get(f"/api/v1/reports/{ids['client']}/download")
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/pdf"
    assert 'attachment; filename="shop.example.com-2026-10-client-report.pdf"' == dl.headers["content-disposition"]
    assert p["cu"].get(f"/api/v1/reports/{ids['technical']}/download").status_code == 404  # never for clients
    assert p["dev"].get(f"/api/v1/reports/{ids['technical']}/download").status_code == 200
    p["admin"].post(f"/api/v1/reports/{ids['client']}/withdraw")
    assert p["cu"].get(f"/api/v1/reports/{ids['client']}/download").status_code == 404


def test_tampered_file_is_refused(db, site, people, storage):
    gen(people["dev"], site, kind="technical")
    r = db.scalar(select(MonthlyReport))
    (storage / r.file_path).write_bytes(b"%PDF-tampered")
    assert people["dev"].get(f"/api/v1/reports/{r.id}/download").status_code == 409


def test_approve_and_email_with_attachment(db, site, people):
    p = people
    gen(p["dev"], site, kind="client")
    rid = db.scalar(select(MonthlyReport.id))
    r = p["admin"].post(f"/api/v1/reports/{rid}/approve", json={"email": True})
    assert r.status_code == 200 and r.json()["emailed_to"] == ["ops@acme.example.com"]
    msg = MemoryNotifier.outbox[-1]
    assert msg["subject"] == "[SiteWatch] October 2026 maintenance report: Shop <b>&amp;</b>"
    name, size, mime = msg["attachments"][0]
    assert name == "shop.example.com-2026-10-client-report.pdf" and size > 10000 and mime == "application/pdf"
    p["admin"].post(f"/api/v1/reports/{rid}/approve", json={"email": True})
    assert len(MemoryNotifier.outbox) == 1  # same generation is not emailed twice


# ---- scheduling & concurrency ---------------------------------------------------------------

def test_scheduled_generation_idempotent(db, site, make_client, make_website):
    paused = make_website(site.client, "Paused", "https://paused.example.com/", status="paused")
    late = make_website(site.client, "New", "https://new.example.com/")  # created after October
    assert rsvc.schedule_monthly(db, now=datetime(2026, 11, 1, 9, tzinfo=UTC)) == []  # before generation day (2)
    ids = rsvc.schedule_monthly(db, now=datetime(2026, 11, 2, 9, tzinfo=UTC))
    rows = db.scalars(select(MonthlyReport)).all()
    assert len(ids) == 2 and {(r.website_id, r.period) for r in rows} == {(site.id, PERIOD)}
    assert paused.id not in {r.website_id for r in rows} and late.id not in {r.website_id for r in rows}
    assert rsvc.schedule_monthly(db, now=datetime(2026, 11, 2, 10, tzinfo=UTC)) == []


def test_concurrent_generation_runs_once(db, site):
    rsvc.ensure_rows(db, site, PERIOD, ("technical",))
    db.commit()
    rid = db.scalar(select(MonthlyReport.id))
    locked, release = threading.Event(), threading.Event()

    def holder():
        with SessionLocal() as s:
            s.scalar(select(MonthlyReport).where(MonthlyReport.id == rid).with_for_update())
            locked.set()
            release.wait(5)
            s.rollback()

    th = threading.Thread(target=holder)
    th.start()
    locked.wait(5)
    with SessionLocal() as s2:
        assert rsvc.generate(s2, rid) is None  # row is being generated elsewhere
    release.set()
    th.join()
    with SessionLocal() as s3:
        assert rsvc.generate(s3, rid).status == "ready"


def test_storage_paths_cannot_escape():
    with pytest.raises(rsvc.ReportError):
        rsvc.safe_path("../../etc/passwd")


# ---- maintenance log & branding ---------------------------------------------------------------

def test_maintenance_log_rules(db, site, people):
    p = people
    url = f"/api/v1/websites/{site.id}/maintenance"
    today = datetime.now(UTC).date()
    assert p["cu"].get(url).status_code == 403
    assert p["cu"].post(url, json={"performed_at": str(today), "title": "x"}).status_code == 403
    assert p["dev"].post(url, json={"performed_at": str(today + timedelta(days=1)), "title": "Planned"}).status_code == 422
    r = p["dev"].post(url, json={"performed_at": str(today), "title": "Fixed menu link", "category": "fix",
                                 "issue_id": str(db.scalar(select(Issue.id).where(Issue.fingerprint == "a")))})
    assert r.status_code == 201 and r.json()["performed_by_email"] == p["dev"].user.email
    assert p["dev"].post(url, json={"performed_at": str(today), "title": "x", "category": "magic"}).status_code == 422
    assert p["other_dev"].post(url, json={"performed_at": str(today), "title": "x"}).status_code == 404
    assert len(p["dev"].get(url).json()) == 3
    assert p["dev"].delete(f"{url}/{r.json()['id']}").status_code == 204


def test_branding_settings_and_logo_validation(as_user):
    admin, dev = as_user("super_admin"), as_user("developer")
    assert dev.get("/api/v1/settings/branding").status_code == 403
    assert admin.put("/api/v1/settings/branding", json={"accent_color": "red"}).status_code == 422
    r = admin.put("/api/v1/settings/branding", json={"company_name": "Agency Ltd", "accent_color": "#0f766e"})
    assert r.json()["company_name"] == "Agency Ltd" and r.json()["has_logo"] is False
    up = lambda name, data, mime: admin.post("/api/v1/settings/branding/logo", files={"file": (name, data, mime)})  # noqa
    assert up("x.png", b"not an image", "image/png").status_code == 415
    assert up("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml").status_code == 415
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    assert up("x.gif", gif.getvalue(), "image/gif").status_code == 415
    assert up("big.png", b"\x89PNG" + b"0" * (1024 * 1024 + 10), "image/png").status_code == 413
    png = io.BytesIO()
    Image.new("RGBA", (2400, 800), (0, 0, 0, 0)).save(png, format="PNG")
    r = up("logo.png", png.getvalue(), "image/png")
    assert r.status_code == 200 and r.json()["width"] <= 1200
    assert admin.get("/api/v1/settings/branding").json()["has_logo"] is True


def test_unreachable_audit_is_grouped_and_plain_for_clients(db, make_client, make_website):
    w = make_website(make_client(), "Down", "https://down.example.com/")
    reason = "The site could not be crawled: robots.txt request failed (DNS lookup failed for down.example.com: Name or service not known)"
    cov = {k: {"status": "not_checked", "reason": reason} for k in ("broken_internal_link", "missing_title", "sitemap")}
    db.add(AuditRun(website_id=w.id, trigger="manual", status="partial", finished_at=OCT + timedelta(days=3),
                    pages_crawled=0, summary={"by_severity": {}, "counts": {}}, check_coverage=cov))
    db.add(SslCertificate(website_id=w.id, hostname="down.example.com", checked_at=OCT + timedelta(days=3), san=[],
                          check_error="DNS lookup failed for down.example.com: Name or service not known"))
    db.commit()
    tech = text_of(render_pdf(build_snapshot(db, w, PERIOD, "technical", now=NOW)))
    client = text_of(render_pdf(build_snapshot(db, w, PERIOD, "client", now=NOW)))
    assert tech.count("Name or service not known") >= 1 and tech.count("could not be crawled") == 1  # grouped
    assert "Name or service not known" not in client and "DNS" not in client
    assert "The audit could not reach the website" in client
    assert "No outstanding issues." not in client and "does not mean there are none" in client
    snap = build_snapshot(db, w, PERIOD, "technical", now=NOW)
    assert any("could not access the website" in x for x in snap["summary"])
    assert snap["recommendations"][0]["text"].startswith("Make the website reachable")
