import csv
import io
import json

import pytest
from sqlalchemy import select

from app.audit.checks import CHECKS

from app.models import AuditFinding, AuditRun, Issue
from app.notify.senders import MemoryNotifier
from tests.auditsite import Site, defect_site, materialize, page
from tests.test_audit import FAST


@pytest.fixture
def env(settings, make_client, make_website, as_user, db):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with Site() as ext, Site() as site:
        ext.routes.update({"/ok": page("Ext", "e"), "/dead": {"status": 404, "body": "gone"}})
        routes = materialize(defect_site(ext.base), site.base)
        # crawled content that tries a spreadsheet formula injection via the page title
        routes["/about"] = page("=HYPERLINK(\"http://evil\")", None, links=["/deep1", "/team", "/"])
        site.routes.update(routes)
        acme, globex = make_client("Acme"), make_client("Globex")
        dev, admin = as_user("developer"), as_user("super_admin")
        acme_cu, other_dev = as_user("client", acme), as_user("developer")
        w = make_website(acme, "Shop", site.base + "/", crawl_config=dict(FAST),
                         notification_recipients=["ops@example.com"], assign=[dev.user, acme_cu.user])
        g = make_website(globex, "Other", "https://other.example.com/")
        yield dict(site=site, w=w, g=g, dev=dev, admin=admin, cu=acme_cu, other_dev=other_dev)


def start(c, w):
    return c.post(f"/api/v1/websites/{w.id}/audits")


def test_manual_audit_end_to_end_via_api(env, db):
    e = env
    assert start(e["cu"], e["w"]).status_code == 403
    assert start(e["other_dev"], e["w"]).status_code == 404
    r = start(e["dev"], e["w"])  # eager: runs synchronously in tests
    assert r.status_code == 202
    run_id = r.json()["id"]
    detail = e["dev"].get(f"/api/v1/audits/{run_id}").json()
    assert detail["status"] == "completed" and detail["pages_crawled"] >= 8
    cov = {c["check_key"]: c for c in detail["coverage"]}
    assert cov["javascript_rendering"]["status"] == "not_checked" and cov["javascript_rendering"]["reason"]
    assert len(detail["coverage"]) == len(CHECKS)  # every check is listed, performed or not
    assert all(c["category"] for c in detail["coverage"])

    f = e["dev"].get(f"/api/v1/audits/{run_id}/findings?check_key=broken_internal_link").json()
    assert {x["severity"] for x in f} == {"high"} and len(f) == 2
    confirmed = e["dev"].get(f"/api/v1/audits/{run_id}/findings?certainty=could_not_check").json()
    assert {x["check_key"] for x in confirmed} >= {"robots_blocked_pages", "page_access_restricted"}

    subjects = [m["subject"] for m in MemoryNotifier.outbox]
    assert "[SiteWatch] Monthly audit completed: Shop" in subjects
    assert "[SiteWatch] Critical audit findings: Shop" in subjects

    cmp = e["dev"].get(f"/api/v1/audits/{run_id}/compare").json()
    assert cmp["previous_run_id"] is None and len(cmp["new"]) > 5 and cmp["by_severity"]["high"]["previous"] is None


def test_audit_busy_and_archived(env, db):
    e = env
    run = AuditRun(website_id=e["w"].id, trigger="manual", status="running")
    db.add(run)
    db.commit()
    assert start(e["dev"], e["w"]).status_code == 409
    e["g"].status = "archived"
    db.commit()
    assert start(e["admin"], e["g"]).status_code == 409


def test_audit_reads_are_staff_only_and_scoped(env):
    e = env
    run_id = start(e["dev"], e["w"]).json()["id"]
    for path in ("", "/findings", "/compare", "/export"):
        assert e["cu"].get(f"/api/v1/audits/{run_id}{path}").status_code == 403
        assert e["other_dev"].get(f"/api/v1/audits/{run_id}{path}").status_code == 404
    assert [r["id"] for r in e["dev"].get("/api/v1/audits").json()] == [run_id]
    assert e["other_dev"].get("/api/v1/audits").json() == []


def test_exports(env):
    e = env
    run_id = start(e["dev"], e["w"]).json()["id"]
    r = e["dev"].get(f"/api/v1/audits/{run_id}/export?format=csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["check", "severity", "certainty", "title", "url", "found_on", "detail"]
    # No cell may start with a spreadsheet formula character (crawled content is untrusted).
    assert not any(cell[:1] in ("=", "+", "-", "@") for row in rows[1:] for cell in row)
    assert any("missing meta description" in row[3].lower() for row in rows[1:])
    j = e["dev"].get(f"/api/v1/audits/{run_id}/export?format=json").json()
    assert j["website"]["name"] == "Shop" and "coverage" in j and len(j["findings"]) == len(rows) - 1


def test_issue_views_and_workflow(env, db):
    e = env
    start(e["dev"], e["w"])
    staff = e["dev"].get("/api/v1/issues").json()
    client = e["cu"].get("/api/v1/issues").json()
    assert staff and client
    assert set(client[0]) == {"id", "website_id", "website_name", "category", "title", "severity", "status",
                              "first_seen", "resolved_at"}
    assert all(i["severity"] != "info" for i in client)
    assert e["other_dev"].get("/api/v1/issues").json() == []

    issue = next(i for i in staff if i["check_key"] == "broken_internal_link")
    iid = issue["id"]
    assert e["cu"].patch(f"/api/v1/issues/{iid}", json={"status": "in_progress"}).status_code == 403
    assert e["dev"].patch(f"/api/v1/issues/{iid}", json={"status": "resolved"}).status_code == 422
    r = e["dev"].patch(f"/api/v1/issues/{iid}", json={"status": "in_progress", "assignee_id": str(e["dev"].user.id)})
    assert r.status_code == 200 and r.json()["assignee_email"] == e["dev"].user.email
    # assignee must be staff with access to the site
    assert e["dev"].patch(f"/api/v1/issues/{iid}", json={"assignee_id": str(e["other_dev"].user.id)}).status_code == 422
    assert e["dev"].patch(f"/api/v1/issues/{iid}", json={"assignee_id": str(e["cu"].user.id)}).status_code == 422
    assert e["cu"].get(f"/api/v1/issues/{iid}").json()["status"] == "Being worked on"

    # internal notes: staff only
    assert e["dev"].post(f"/api/v1/issues/{iid}/comments", json={"body": "Fixed link in CMS menu"}).status_code == 201
    assert e["cu"].get(f"/api/v1/issues/{iid}/comments").status_code == 403
    assert e["cu"].post(f"/api/v1/issues/{iid}/comments", json={"body": "x"}).status_code == 403
    notes = e["dev"].get(f"/api/v1/issues/{iid}/comments").json()
    assert notes[0]["body"] == "Fixed link in CMS menu"
    assert "comment_count" not in e["cu"].get(f"/api/v1/issues/{iid}").json()
    assert e["other_dev"].get(f"/api/v1/issues/{iid}/comments").status_code == 404


def test_could_not_check_findings_never_become_issues(env, db):
    e = env
    start(e["dev"], e["w"])
    uncertain = {f.fingerprint for f in db.scalars(select(AuditFinding).where(AuditFinding.certainty == "could_not_check"))}
    assert uncertain
    assert not (uncertain & {i.fingerprint for i in db.scalars(select(Issue))})


def test_findings_json_detail_is_serializable(env):
    e = env
    run_id = start(e["dev"], e["w"]).json()["id"]
    for f in e["dev"].get(f"/api/v1/audits/{run_id}/findings").json():
        json.dumps(f)


def test_csv_export_neutralises_formulas(env, db):
    e = env
    run = AuditRun(website_id=e["w"].id, trigger="manual", status="completed")
    db.add(run)
    db.flush()
    db.add(AuditFinding(audit_run_id=run.id, website_id=e["w"].id, check_key="missing_title", severity="low",
                        certainty="confirmed", title='=HYPERLINK("http://evil","x")', url="+cmd", fingerprint="f"))
    db.commit()
    rows = list(csv.reader(io.StringIO(e["dev"].get(f"/api/v1/audits/{run.id}/export?format=csv").text)))
    assert rows[1][3] == "'=HYPERLINK(\"http://evil\",\"x\")" and rows[1][4] == "'+cmd"
