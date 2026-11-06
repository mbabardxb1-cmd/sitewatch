"""Audit categories, the new content/security/form checks, progress reporting and run deletion."""
import pytest
from sqlalchemy import select

from app.audit import service
from app.audit.checks import CATEGORIES, CATEGORY_OF, CHECKS
from app.audit.htmlparse import parse_html
from app.models import AuditFinding, AuditRun, Issue
from tests.auditsite import Site, page
from tests.test_audit import FAST

FORM_PAGE = (
    '<!doctype html><html lang="en"><head><title>Contact</title><meta name="description" content="c">'
    '<meta name="viewport" content="width=device-width"></head><body><h1>Contact</h1>'
    '<form action="/send" method="post"><input name="email"><textarea name="msg" aria-label="Message"></textarea>'
    '<button>Send</button></form>'
    '<form action="http://insecure.example/login" method="post"><label>User <input name="u"></label>'
    '<input type="password" name="p" title="Password"></form>'
    '<script src="http://cdn.example/x.js"></script></body></html>'
)


def test_every_check_belongs_to_exactly_one_category():
    listed = [c for cat in CATEGORIES.values() for c in cat["checks"]]
    assert sorted(listed) == sorted(CHECKS) and len(listed) == len(set(listed))


def test_parser_extracts_forms_headings_and_resources():
    f = parse_html(FORM_PAGE, "https://site.example/contact")
    assert f.h1_count == 1 and f.lang == "en" and "width" in f.viewport
    assert "http://cdn.example/x.js" in f.resources
    contact, login = f.forms
    assert (contact.action, contact.method, contact.fields, contact.unlabeled) == ("https://site.example/send", "post", 2, 1)
    assert login.has_password and login.unlabeled == 0 and not f.spam_protection
    assert parse_html('<div class="g-recaptcha"></div>', "https://x/").spam_protection


@pytest.fixture
def form_site(settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with Site() as s:
        s.routes.update({
            "/robots.txt": {"type": "text/plain", "body": "User-agent: *\n"},
            "/": page("Home", "Welcome", links=["/contact"]),
            "/contact": {"body": FORM_PAGE},
        })
        yield s


def _run(db, site, categories=None, progress=None):
    run = service.create_manual_run(db, site, None, categories)
    db.commit()
    service.execute_run(db, run.id, progress=progress)
    db.expire_all()
    return db.get(AuditRun, run.id)


def test_form_checks_never_submit_and_report_problems(db, make_client, make_website, form_site):
    w = make_website(make_client(), "Forms", form_site.base + "/", crawl_config=dict(FAST))
    run = _run(db, w)
    keys = {f.check_key for f in db.scalars(select(AuditFinding).where(AuditFinding.audit_run_id == run.id))}
    assert {"form_action", "form_spam_protection", "form_labels", "heading_structure", "missing_lang"} <= keys
    # The form target was only looked up (HEAD/GET), never POSTed to.
    assert not [h for h in form_site.httpd.hits if h[0] == "POST"]
    assert run.check_coverage["form_action"]["status"] in ("performed", "partial")


def test_unselected_categories_are_not_checked_or_resolved(db, make_client, make_website, form_site):
    w = make_website(make_client(), "Forms", form_site.base + "/", crawl_config=dict(FAST))
    full = _run(db, w)
    open_forms = db.scalars(select(Issue).where(Issue.website_id == w.id, Issue.check_key == "form_labels")).all()
    assert open_forms
    seen: list[dict] = []
    seo_only = _run(db, w, ["seo"], progress=seen.append)
    assert seo_only.config_snapshot["categories"] == ["seo"] and seo_only.summary["categories"] == ["seo"]
    for key, cov in seo_only.check_coverage.items():
        if CATEGORY_OF[key] != "seo":
            assert cov == {"status": "not_checked", "reason": "Not selected for this audit"}
    findings = db.scalars(select(AuditFinding).where(AuditFinding.audit_run_id == seo_only.id)).all()
    assert findings and all(CATEGORY_OF[f.check_key] == "seo" for f in findings)
    db.expire_all()
    # Form issues are not "resolved" by an audit that did not look at forms.
    assert all(db.get(Issue, i.id).status == "open" for i in open_forms)
    assert seen and seen[-1]["percent"] <= 99 and seo_only.progress["percent"] == 100
    assert full.id != seo_only.id


def test_start_audit_api_validates_categories_and_delete(db, make_client, make_website, as_user, form_site):
    w = make_website(make_client(), "Forms", form_site.base + "/", crawl_config=dict(FAST))
    admin, dev = as_user("super_admin"), as_user("developer")
    cats = dev.get("/api/v1/audit-categories").json()
    assert {c["key"] for c in cats} == set(CATEGORIES)
    assert admin.post(f"/api/v1/websites/{w.id}/audits", json={"categories": ["nope"]}).status_code == 422
    r = admin.post(f"/api/v1/websites/{w.id}/audits", json={"categories": ["forms", "mobile"]})
    assert r.status_code == 202 and r.json()["categories"] == ["forms", "mobile"]
    run_id = r.json()["id"]
    assert db.scalar(select(Issue.id).where(Issue.first_seen_run_id == run_id)) is not None
    assert dev.delete(f"/api/v1/audits/{run_id}").status_code == 403
    assert admin.delete(f"/api/v1/audits/{run_id}").status_code == 204
    db.expire_all()
    assert db.get(AuditRun, run_id) is None
    assert db.scalar(select(Issue.id).where(Issue.website_id == w.id)) is None


def test_stop_queued_and_running_audits(db, make_client, make_website, as_user, form_site):
    from datetime import UTC, datetime

    from app.db import SessionLocal

    w = make_website(make_client(), "Forms", form_site.base + "/", crawl_config=dict(FAST))
    dev = as_user("super_admin")
    # queued -> stopped before it starts; the worker then skips it
    run = service.create_manual_run(db, w, None)
    db.commit()
    r = dev.post(f"/api/v1/audits/{run.id}/stop")
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and "before it started" in r.json()["error"]
    assert service.execute_run(db, run.id) is None
    assert dev.post(f"/api/v1/audits/{run.id}/stop").status_code == 409

    # running -> the crawl notices at its next progress update and keeps nothing
    run = service.create_manual_run(db, w, None)
    db.commit()
    writer = service.ProgressWriter(run.id, None, min_interval=0)
    calls = []

    def progress(p):
        calls.append(p)
        if len(calls) == 3:
            with SessionLocal() as s:
                service.stop_run(s, s.get(AuditRun, run.id), "tester@example.com")
                s.commit()
        writer(p)

    service.execute_run(db, run.id, progress=progress)
    db.expire_all()
    stopped = db.get(AuditRun, run.id)
    assert stopped.status == "cancelled" and stopped.error == "Stopped by tester@example.com"
    assert len(calls) == 3  # crawl stopped right away
    assert db.scalar(select(AuditFinding.id).where(AuditFinding.audit_run_id == run.id)) is None
    assert db.scalar(select(Issue.id).where(Issue.website_id == w.id)) is None
    assert stopped.finished_at <= datetime.now(UTC)


def test_orphaned_running_audit_is_failed_after_heartbeat_stops(db, make_client, make_website):
    from datetime import UTC, datetime, timedelta

    w = make_website(make_client(), "Site", "https://site.example.com/")
    now = datetime.now(UTC)
    old = AuditRun(website_id=w.id, trigger="manual", status="running", started_at=now - timedelta(minutes=40),
                   progress={"percent": 30, "updated_at": (now - timedelta(minutes=20)).isoformat()})
    alive = AuditRun(website_id=w.id, trigger="manual", status="running", started_at=now - timedelta(minutes=40),
                     progress={"percent": 30, "updated_at": (now - timedelta(minutes=1)).isoformat()})
    never = AuditRun(website_id=w.id, trigger="manual", status="running", started_at=now - timedelta(minutes=16), progress={})
    db.add_all([old, alive, never])
    db.commit()
    assert service.fail_stuck_runs(db) == 2
    db.expire_all()
    assert (old.status, alive.status, never.status) == ("failed", "running", "failed")
    assert "restarted" in old.error
