"""Crawler + audit engine against real local sites with known defects."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.audit import service
from app.audit.crawler import Crawler
from app.models import AuditFinding, AuditRun, Issue, Website
from tests.auditsite import Site, defect_site, materialize, page

FAST = {"max_depth": 2, "max_pages": 100, "timeout_s": 3, "rate_rps": 20, "excluded_patterns": [],
        "check_external_links": True, "render_js": False, "slow_page_ms": 250}


@pytest.fixture
def sites(settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with Site() as ext, Site() as site:
        ext.routes.update({"/ok": page("Ext", "e"), "/dead": {"status": 404, "body": "gone"}})
        site.routes.update(materialize(defect_site(ext.base), site.base))
        yield site, ext


@pytest.fixture
def website(db, make_client, make_website, sites):
    site, _ = sites
    return make_website(make_client("Acme"), "Shop", site.base + "/", crawl_config=dict(FAST),
                        notification_recipients=["ops@example.com"])


def run_audit(db, website) -> AuditRun:
    run = service.create_manual_run(db, website, None)
    db.commit()
    service.execute_run(db, run.id)
    db.expire_all()
    return db.get(AuditRun, run.id)


def findings(db, run):
    return db.scalars(select(AuditFinding).where(AuditFinding.audit_run_id == run.id)).all()


def by_check(fs, key):
    return sorted(f.url or f.title for f in fs if f.check_key == key)


def test_full_audit_detects_known_defects(db, website, sites):
    site, ext = sites
    B, E = site.base, ext.base
    run = run_audit(db, website)
    assert run.status == "completed", run.error
    fs = findings(db, run)

    assert by_check(fs, "broken_internal_link") == sorted([f"{B}/error500", f"{B}/missing"])
    missing = next(f for f in fs if f.url == f"{B}/missing")
    assert missing.severity == "high" and missing.certainty == "confirmed"
    assert missing.detail["linked_from"] == [f"{B}/"]
    assert by_check(fs, "redirect_chain") == [f"{B}/r1"]
    assert by_check(fs, "redirect_loop") == [f"{B}/loop-a"]
    assert by_check(fs, "page_access_restricted") == [f"{B}/members"]
    assert by_check(fs, "slow_page") == [f"{B}/slow"]
    assert by_check(fs, "missing_meta_description") == [f"{B}/about"]
    dup = [f for f in fs if f.check_key == "duplicate_title"]
    assert len(dup) == 1 and sorted(dup[0].detail["urls"]) == [f"{B}/about", f"{B}/team"]
    assert by_check(fs, "broken_image") == [f"{B}/broken.png"]
    assert by_check(fs, "missing_alt") == [f"{B}/"]

    ext_f = {f.url: f for f in fs if f.check_key == "broken_external_link"}
    assert ext_f[f"{E}/dead"].certainty == "confirmed"
    meta = ext_f["http://169.254.169.254/latest/meta-data/"]
    assert meta.certainty == "could_not_check"  # SSRF guard: we refused, so we can't claim it's broken
    assert f"{E}/ok" not in ext_f

    sm = {f.title for f in fs if f.check_key == "sitemap"}
    assert any("gone-in-sitemap" in t and "404" in t for t in sm)
    assert any("disallowed by robots.txt" in t and "/private/secret" in t for t in sm)
    blocked = next(f for f in fs if f.check_key == "robots_blocked_pages")
    assert blocked.detail["urls"] == [f"{B}/private/x"] and blocked.certainty == "could_not_check"
    # robots.txt was respected: nothing under /private/ was requested
    assert not any(p.startswith("/private/") for _, p in site.hits)
    # depth limit (max_depth=2): /deep2 sits one level beyond the limit, so it is verified as a link
    # target but not parsed; /deep3 (linked only from /deep2) is therefore never discovered.
    hit_paths = [p for _, p in site.hits]
    assert "/deep2" in hit_paths and "/deep3" not in hit_paths
    assert f"{B}/deep2" not in [f.url for f in fs if f.check_key == "missing_title"]

    cov = run.check_coverage
    assert cov["broken_internal_link"]["status"] == "performed"
    assert cov["ssl_certificate"]["status"] == "not_applicable"
    assert cov["javascript_rendering"]["status"] == "not_checked"
    # First audit: nothing is flagged important yet; the homepage gets flagged for later runs.
    assert cov["important_page_unavailable"]["status"] == "not_applicable"
    assert run.summary["pages_crawled"] >= 8 and run.summary["broken_internal_links"] == 2
    assert run.summary["counts"]["new"] == len([f for f in fs if f.certainty != "could_not_check"])
    assert db.get(Website, website.id).last_audit_at is not None


def test_issues_resolve_only_when_verified_and_recur(db, website, sites):
    site, ext = sites
    B, E = site.base, ext.base
    run1 = run_audit(db, website)
    issues = {i.url: i for i in db.scalars(select(Issue).where(Issue.check_key.like("broken_%")))}
    assert issues[f"{B}/missing"].status == "open"

    # Fix /missing, remove the link to the dead external page, break a new page.
    site.routes["/missing"] = page("Missing no more", "fixed")
    root = site.routes["/"]["body"].replace(f'<a href="{E}/dead">link</a>', "")
    site.routes["/"] = {"body": root.replace('<a href="/about">', '<a href="/new-broken"><a href="/about">')}
    run2 = run_audit(db, website)
    db.expire_all()
    issues = {i.url: i for i in db.scalars(select(Issue).where(Issue.check_key.like("broken_%")))}
    fixed = issues[f"{B}/missing"]
    assert fixed.status == "resolved" and fixed.resolved_verified_run_id == run2.id
    assert "no longer observed" in fixed.resolution_note
    dead = issues[f"{E}/dead"]
    assert dead.status == "resolved" and "no remaining references" in dead.resolution_note
    assert issues[f"{B}/new-broken"].status == "open" and issues[f"{B}/new-broken"].first_seen_run_id == run2.id
    assert issues[f"{B}/error500"].status == "open" and issues[f"{B}/error500"].occurrences == 2
    assert run2.summary["counts"]["resolved"] >= 2
    # The only new link problem is /new-broken (the now-served /missing page may add content findings).
    new_link = [i for i in db.scalars(select(Issue).where(Issue.first_seen_run_id == run2.id))
                if i.check_key.startswith("broken_")]
    assert [i.url for i in new_link] == [f"{B}/new-broken"]

    # Break /missing again -> same issue re-opens and is counted as recurring.
    site.routes["/missing"] = {"status": 404, "body": "nope"}
    run3 = run_audit(db, website)
    db.expire_all()
    again = db.scalar(select(Issue).where(Issue.url == f"{B}/missing", Issue.check_key == "broken_internal_link"))
    assert again.status == "open" and again.reopened_count == 1 and again.resolution_note is None
    assert str(again.id) in run3.summary["recurring"]
    assert run1.id != run2.id != run3.id


def test_partial_crawl_does_not_resolve_unverified_issues(db, website, sites):
    site, _ = sites
    run_audit(db, website)
    website.crawl_config = {**FAST, "max_pages": 2}
    db.commit()
    run = run_audit(db, website)
    assert run.status == "partial"
    assert run.check_coverage["broken_internal_link"]["status"] == "partial"
    assert "page limit" in run.check_coverage["broken_internal_link"]["reason"]
    still = db.scalar(select(Issue).where(Issue.url == f"{site.base}/error500"))
    assert still.status == "open"  # not re-checked, so not claimed fixed


def test_pending_verification_reopens_if_still_broken(db, website, sites):
    site, _ = sites
    run_audit(db, website)
    issue = db.scalar(select(Issue).where(Issue.url == f"{site.base}/missing"))
    issue.status = "resolved_pending_verification"
    db.commit()
    run_audit(db, website)
    db.expire_all()
    assert db.get(Issue, issue.id).status == "open"


def test_unreachable_site_checks_nothing_and_resolves_nothing(db, make_client, make_website, settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with Site() as s:
        dead_base = s.base
    site = make_website(make_client(), "Down", dead_base + "/", crawl_config=dict(FAST))
    run = run_audit(db, site)
    assert run.status == "partial" and run.pages_crawled == 0
    cov = run.check_coverage
    assert cov["broken_internal_link"]["status"] == "not_checked"
    assert "robots.txt request failed" in cov["broken_internal_link"]["reason"]
    assert db.scalars(select(Issue).where(Issue.status == "resolved")).all() == []


def test_robots_disallow_all_is_respected_and_reported(db, make_client, make_website, settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with Site({"/robots.txt": {"type": "text/plain", "body": "User-agent: *\nDisallow: /\n"},
               "/": page("Home", "x")}) as s:
        site = make_website(make_client(), "Hidden", s.base + "/", crawl_config=dict(FAST))
        run = run_audit(db, site)
        assert [p for _, p in s.hits] == ["/robots.txt"]  # nothing else requested
    fs = findings(db, run)
    assert any(f.check_key == "robots_txt" and f.severity == "high" and "disallows all" in f.title for f in fs)
    assert "disallows crawling the homepage" in run.check_coverage["missing_title"]["reason"]


def test_invalid_sitemap_and_noindex_homepage(db, make_client, make_website, settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with Site({"/sitemap.xml": {"type": "application/xml", "body": "<urlset><url><loc>broken"},
               "/": page("Home", "x", robots="noindex,follow")}) as s:
        site = make_website(make_client(), "S", s.base + "/", crawl_config=dict(FAST))
        run = run_audit(db, site)
    fs = findings(db, run)
    assert any(f.check_key == "sitemap" and "invalid" in f.title for f in fs)
    assert any(f.check_key == "indexing" and f.severity == "high" for f in fs)
    assert any(f.check_key == "robots_txt" and f.severity == "info" for f in fs)  # no robots.txt


def test_excluded_patterns(db, website, sites):
    site, _ = sites
    website.crawl_config = {**FAST, "excluded_patterns": ["/deep*", "re:^/r[0-9]"]}
    db.commit()
    run = run_audit(db, website)
    hits = [p for _, p in site.hits]
    assert not any(p.startswith("/deep") or p in ("/r1", "/r2") for p in hits[-40:])
    assert run.summary["excluded"] >= 2


def test_important_pages(db, website, sites, as_user):
    site, _ = sites
    admin = as_user("super_admin")
    r = admin.put(f"/api/v1/websites/{website.id}/important-urls", json={"urls": ["/pricing", "/about"]})
    assert r.status_code == 200 and len(r.json()) == 2
    bad = admin.put(f"/api/v1/websites/{website.id}/important-urls", json={"urls": ["https://other.example.com/x"]})
    assert bad.status_code == 422
    run = run_audit(db, website)
    fs = findings(db, run)
    assert by_check(fs, "important_page_unavailable") == [f"{site.base}/pricing"]


def test_rate_limit_and_crawl_delay(settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    slept: list[float] = []
    clock = [0.0]

    def fake_sleep(s):
        slept.append(s)
        clock[0] += s

    with Site({"/robots.txt": {"type": "text/plain", "body": "User-agent: *\nCrawl-delay: 2\n"},
               "/": page("H", "d", links=["/a", "/b"]), "/a": page("A", "a"), "/b": page("B", "b")}) as s:
        Crawler(s.base + "/", {**FAST, "rate_rps": 4, "check_external_links": False},
                sleep=fake_sleep, monotonic=lambda: clock[0]).run()
    assert slept and all(abs(x - 2.0) < 1e-6 for x in slept)  # Crawl-delay (2s) overrides 4 req/s


def test_scheduled_runs_are_idempotent(db, make_client, make_website):
    acme = make_client()
    due = make_website(acme, "Due", "https://due.example.com", audit_day_of_month=2)
    make_website(acme, "Later", "https://later.example.com", audit_day_of_month=20)
    make_website(acme, "Paused", "https://p.example.com", audit_day_of_month=1, status="paused")
    off = make_website(acme, "Manual only", "https://manual.example.com", audit_day_of_month=1)
    off.auto_audit = False
    for w in db.scalars(select(Website)):
        w.created_at = datetime(2026, 10, 1, tzinfo=UTC)
    db.commit()
    now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    first = service.create_due_scheduled_runs(db, now)
    second = service.create_due_scheduled_runs(db, now)
    assert len(first) == 1 and second == []
    run = db.get(AuditRun, first[0])
    assert (run.website_id, run.period, run.trigger) == (due.id, "2026-11", "scheduled")
    assert service.create_due_scheduled_runs(db, datetime(2026, 12, 2, tzinfo=UTC))  # next month creates again


def test_site_added_after_audit_day_waits_for_next_month(db, make_client, make_website):
    acme = make_client()
    site = make_website(acme, "Fresh", "https://fresh.example.com", audit_day_of_month=1)
    site.created_at = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    db.commit()
    assert service.create_due_scheduled_runs(db, datetime(2026, 11, 7, tzinfo=UTC)) == []
    assert len(service.create_due_scheduled_runs(db, datetime(2026, 12, 1, 1, 0, tzinfo=UTC))) == 1


def test_duplicate_task_delivery_runs_once(db, website):
    run = service.create_manual_run(db, website, None)
    db.commit()
    assert service.claim_run(db, run.id) is not None
    assert service.claim_run(db, run.id) is None


def test_ssl_inspection_self_signed(settings):
    from app.netsafe.tls import inspect_certificate
    from tests.httpfixtures import LocalSite

    settings.private_target_allowlist = ["127.0.0.1/32"]
    with LocalSite(tls=True) as s:
        info = inspect_certificate("127.0.0.1", s.httpd.server_port, timeout=3)
    assert info.valid is False and "self" in (info.error or "").lower()
    assert info.not_after is not None and 0 <= info.days_remaining() <= 2


def test_ssl_inspection_blocked_without_allowlist():
    from app.netsafe.http import BlockedTargetError
    from app.netsafe.tls import inspect_certificate

    with pytest.raises(BlockedTargetError):
        inspect_certificate("127.0.0.1", 443, timeout=1)
