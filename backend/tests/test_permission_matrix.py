"""Full authorization matrix: every API route x every kind of actor.

For each route we declare which permissions it needs and whether it addresses a resource owned
by tenant A. The expected outcome for each actor is then derived from the role catalogue
(app/auth/permissions.py), not hand-written per case:

  anonymous                        -> 401
  missing permission               -> 403
  resource of another tenant       -> 404 (indistinguishable from "does not exist")
  otherwise                        -> allowed (never 401/403/404/5xx)

Additionally, for actors outside tenant A, no allowed response may contain any identifier of
tenant A's websites, incidents, issues, audits, reports or maintenance entries, and every
state-changing route must reject requests without the CSRF token.

`test_every_route_is_classified` fails when a new endpoint is added without being listed here.
"""
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from app.auth.permissions import ROLES
from app.auth.security import hash_password, hash_token, new_token
from app.main import app
from app.models import (
    AuditFinding,
    AuditRun,
    Client,
    HostingIntegration,
    Issue,
    IssueComment,
    MaintenanceActivity,
    MonthlyReport,
    NotificationLog,
    NotificationRule,
    Role,
    UptimeCheck,
    UptimeIncident,
    User,
    UserSession,
    Website,
)
from tests.conftest import ApiClient

PW_HASH = hash_password("Unused-Password-1")  # computed once; matrix actors use direct sessions
PERIOD = datetime.now(UTC).strftime("%Y-%m")
ACTORS = ["anon", "client_a", "client_b", "dev_unassigned", "dev_a", "admin"]
ROLE_OF = {"client_a": "client", "client_b": "client", "dev_unassigned": "developer", "dev_a": "developer",
           "admin": "super_admin"}
IN_SCOPE_A = {"client_a", "dev_a", "admin"}

PUBLIC, AUTH = "public", "authenticated"


def png_bytes():
    b = io.BytesIO()
    Image.new("RGB", (20, 10)).save(b, format="PNG")
    return b.getvalue()


# (method, path) -> (permissions [all required] or PUBLIC/AUTH, scoped_to_site_A, request kwargs factory)
def J(body):
    return lambda w: {"json": body(w) if callable(body) else body}


ROUTES = {
    ("GET", "/api/healthz"): (PUBLIC, False, None),
    ("GET", "/api/readyz"): (PUBLIC, False, None),
    ("POST", "/api/v1/auth/login"): ("login", False, J({"email": "nobody@example.com", "password": "x"})),
    ("GET", "/api/v1/auth/me"): (AUTH, False, None),
    ("POST", "/api/v1/auth/logout"): (AUTH, False, None),
    ("POST", "/api/v1/auth/change-password"): (AUTH, False, J({"current_password": "wrong", "new_password": "Xx-123456789"})),
    # users
    ("GET", "/api/v1/users"): (["user.manage"], False, None),
    ("POST", "/api/v1/users"): (["user.manage"], False, J(lambda w: {"email": f"n{uuid.uuid4().hex[:6]}@example.com",
                                                                       "password": "Brand-New-Pass-1", "role": "developer"})),
    ("GET", "/api/v1/users/{user_id}"): (["user.manage"], False, None),
    ("PATCH", "/api/v1/users/{user_id}"): (["user.manage"], False, J({"full_name": "Renamed"})),
    ("PUT", "/api/v1/users/{user_id}/role"): (["user.manage"], False, J({"role": "developer"})),
    ("POST", "/api/v1/users/{user_id}/activate"): (["user.manage"], False, None),
    ("POST", "/api/v1/users/{user_id}/deactivate"): (["user.manage"], False, None),
    # clients
    ("GET", "/api/v1/clients"): (["client.read"], False, None),
    ("GET", "/api/v1/clients/{client_id}"): (["client.read"], False, None),
    ("POST", "/api/v1/clients"): (["client.manage"], False, J(lambda w: {"name": "New", "slug": f"new-{uuid.uuid4().hex[:6]}"})),
    ("PATCH", "/api/v1/clients/{client_id}"): (["client.manage"], False, J({"contact_email": "it@a.example.com"})),
    # websites
    ("GET", "/api/v1/websites"): (["website.read"], False, None),
    ("POST", "/api/v1/websites"): (["website.write"], False, J(lambda w: {
        "client_id": str(w["client_a"].id), "name": "New site", "base_url": f"https://n{uuid.uuid4().hex[:6]}.example.com"})),
    ("GET", "/api/v1/websites/{website_id}"): (["website.read"], True, None),
    ("PATCH", "/api/v1/websites/{website_id}"): (["website.write"], True, J({"name": "Renamed site"})),
    ("POST", "/api/v1/websites/{website_id}/archive"): (["website.write"], True, None),
    ("POST", "/api/v1/websites/{website_id}/restore"): (["website.write"], True, None),
    ("PUT", "/api/v1/websites/{website_id}/assignments"): (["website.assign"], True, J({"user_ids": []})),
    ("PUT", "/api/v1/websites/{website_id}/crawl-config"): (["website.crawl_config"], True, J({})),
    ("GET", "/api/v1/websites/{website_id}/important-urls"): (["audit.read_detailed"], True, None),
    ("PUT", "/api/v1/websites/{website_id}/important-urls"): (["website.crawl_config"], True, J({"urls": ["/pricing"]})),
    # dashboard & monitoring
    ("GET", "/api/v1/dashboard/summary"): (["website.read"], False, None),
    ("GET", "/api/v1/uptime/overview"): (["website.read"], False, None),
    ("GET", "/api/v1/websites/{website_id}/uptime"): (["website.read"], True, None),
    ("GET", "/api/v1/websites/{website_id}/response-times"): (["website.read"], True, None),
    ("GET", "/api/v1/websites/{website_id}/checks"): (["uptime.read_detailed"], True, None),
    ("POST", "/api/v1/websites/{website_id}/check-now"): (["uptime.check_now"], True, None),
    ("GET", "/api/v1/incidents"): (["incident.read"], False, None),
    ("GET", "/api/v1/incidents/{incident_id}"): (["incident.read"], True, None),
    ("POST", "/api/v1/incidents/{incident_id}/acknowledge"): (["incident.manage"], True, None),
    # notifications
    ("GET", "/api/v1/notification-events"): (["notification.read"], False, None),
    ("GET", "/api/v1/notification-logs"): (["notification.read"], False, None),
    ("GET", "/api/v1/notification-rules"): (["notification.manage"], False, None),
    ("POST", "/api/v1/notification-rules"): (["notification.manage"], False, J({"event_type": "website_down", "recipients": ["x@example.com"]})),
    ("PATCH", "/api/v1/notification-rules/{rule_id}"): (["notification.manage"], False, J({"enabled": False})),
    ("DELETE", "/api/v1/notification-rules/{rule_id}"): (["notification.manage"], False, None),
    ("POST", "/api/v1/notifications/test"): (["notification.manage"], False, J({"recipient": "x@example.com"})),
    # audits & issues
    ("GET", "/api/v1/audits"): (["audit.read_detailed"], False, None),
    ("POST", "/api/v1/websites/{website_id}/audits"): (["audit.run"], True, None),
    ("GET", "/api/v1/audits/{run_id}"): (["audit.read_detailed"], True, None),
    ("GET", "/api/v1/audits/{run_id}/findings"): (["audit.read_detailed"], True, None),
    ("GET", "/api/v1/audits/{run_id}/compare"): (["audit.read_detailed"], True, None),
    ("GET", "/api/v1/audits/{run_id}/export"): (["audit.export"], True, None),
    ("GET", "/api/v1/issues"): (["issue.read_summary"], False, None),
    ("GET", "/api/v1/issues/{issue_id}"): (["issue.read_summary"], True, None),
    ("PATCH", "/api/v1/issues/{issue_id}"): (["issue.manage"], True, J({"status": "acknowledged"})),
    ("GET", "/api/v1/issues/{issue_id}/comments"): (["issue.note.read"], True, None),
    ("POST", "/api/v1/issues/{issue_id}/comments"): (["issue.note.write"], True, J({"body": "note"})),
    # expiry & integrations
    ("GET", "/api/v1/expiry/overview"): (["expiry.read"], False, None),
    ("GET", "/api/v1/websites/{website_id}/expiry"): (["expiry.read"], True, None),
    ("PUT", "/api/v1/websites/{website_id}/domain"): (["expiry.write"], True, J({"manual_registrar": "Reg"})),
    ("POST", "/api/v1/websites/{website_id}/domain/refresh"): (["expiry.write"], True, None),
    ("PUT", "/api/v1/websites/{website_id}/hosting"): (["expiry.write"], True, J({"provider": "HostCo"})),
    ("POST", "/api/v1/websites/{website_id}/hosting/refresh"): (["expiry.write"], True, None),
    ("POST", "/api/v1/websites/{website_id}/ssl/refresh"): (["expiry.write"], True, None),
    ("GET", "/api/v1/integrations/hosting"): (["integration.manage"], False, None),
    ("GET", "/api/v1/integrations/hosting/providers"): (["integration.manage"], False, None),
    ("POST", "/api/v1/integrations/hosting"): (["integration.manage"], False, J({"provider_key": "none", "display_name": "x", "credentials": {}})),
    ("DELETE", "/api/v1/integrations/hosting/{integration_id}"): (["integration.manage"], False, None),
    # reports, maintenance, branding
    ("GET", "/api/v1/reports"): (["report.read"], False, None),
    ("POST", "/api/v1/websites/{website_id}/reports"): (["report.generate"], True, J({"period": PERIOD, "kind": "technical", "regenerate": True})),
    ("GET", "/api/v1/reports/{report_id}"): (["report.read"], True, None),
    ("GET", "/api/v1/reports/{report_id}/download"): (["report.read"], True, None),
    ("POST", "/api/v1/reports/{report_id}/approve"): (["report.approve"], True, J({"email": False})),
    ("POST", "/api/v1/reports/{report_id}/withdraw"): (["report.approve"], True, None),
    ("GET", "/api/v1/websites/{website_id}/maintenance"): ("maintenance-read", True, None),
    ("POST", "/api/v1/websites/{website_id}/maintenance"): (["maintenance.write"], True, J(lambda w: {
        "performed_at": datetime.now(UTC).date().isoformat(), "title": "Work"})),
    ("DELETE", "/api/v1/websites/{website_id}/maintenance/{activity_id}"): (["maintenance.write"], True, None),
    ("GET", "/api/v1/settings/system"): (["settings.manage"], False, None),
    ("PUT", "/api/v1/settings/system"): (["settings.manage"], False, J({"report_generation_day": 3})),
    ("GET", "/api/v1/settings/audit-log"): (["settings.manage"], False, None),
    ("GET", "/api/v1/settings/branding"): (["settings.manage"], False, None),
    ("PUT", "/api/v1/settings/branding"): (["settings.manage"], False, J({"company_name": "Agency"})),
    ("POST", "/api/v1/settings/branding/logo"): (["settings.manage"], False,
                                                  lambda w: {"files": {"file": ("l.png", png_bytes(), "image/png")}}),
    ("DELETE", "/api/v1/settings/branding/logo"): (["settings.manage"], False, None),
}


def actor_may(actor: str, perms) -> bool:
    if perms == PUBLIC:
        return True
    if actor == "anon":
        return False
    if perms == AUTH:
        return True
    granted = ROLES[ROLE_OF[actor]][1]
    if perms == "maintenance-read":
        return bool({"maintenance.write", "report.read_all"} & granted)
    return set(perms) <= granted


# ---- world -------------------------------------------------------------------------------

@pytest.fixture
def world(db, settings, tmp_path, monkeypatch):
    settings.storage_dir = str(tmp_path)
    # No network or background work during the matrix: background tasks become no-ops.
    import app.expiry.service as esvc
    import app.worker.tasks as tasks

    for name in ("run_uptime_check", "run_audit", "generate_report", "send_notification"):
        monkeypatch.setattr(getattr(tasks, name), "delay", lambda *a, **k: None)
    monkeypatch.setattr(esvc, "refresh_domain", lambda db, site, now=None: esvc.get_domain_record(db, site))
    monkeypatch.setattr(esvc, "refresh_ssl", lambda db, site, now=None: None)

    def client(name, show):
        c = Client(name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", show_expiry_to_client=show)
        db.add(c)
        return c

    ca, cb = client("TenantA", True), client("TenantB", True)
    db.flush()
    roles = {r.key: r for r in db.query(Role)}

    def user(role, org=None):
        u = User(email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password_hash=PW_HASH, full_name=role,
                 client_id=org.id if org else None)
        u.roles = [roles[role]]
        db.add(u)
        return u

    actors = {"client_a": user("client", ca), "client_b": user("client", cb), "dev_unassigned": user("developer"),
              "dev_a": user("developer"), "admin": user("super_admin")}
    target_user = user("developer")
    site_a = Website(client_id=ca.id, name="Site A", base_url="https://a.example.com/", hostname="a.example.com",
                     next_check_at=None)
    site_b = Website(client_id=cb.id, name="Site B", base_url="https://b.example.com/", hostname="b.example.com")
    site_a.assigned_users = [actors["client_a"], actors["dev_a"]]
    site_b.assigned_users = [actors["client_b"]]
    db.add_all([site_a, site_b])
    db.flush()
    now = datetime.now(UTC)
    incident = UptimeIncident(website_id=site_a.id, started_at=now - timedelta(hours=1), status="open", cause_summary="x")
    run = AuditRun(website_id=site_a.id, trigger="manual", status="completed", finished_at=now, summary={}, check_coverage={})
    db.add_all([incident, run, UptimeCheck(website_id=site_a.id, ok=True, http_status=200, response_ms=100)])
    db.flush()
    issue = Issue(website_id=site_a.id, fingerprint="fp", check_key="missing_title", title="t", severity="high")
    db.add_all([issue, AuditFinding(audit_run_id=run.id, website_id=site_a.id, check_key="missing_title",
                                    severity="high", certainty="confirmed", title="t", fingerprint="fp")])
    db.flush()
    db.add(IssueComment(issue_id=issue.id, body="internal"))
    pdf = b"%PDF-1.4 matrix"
    rel = f"reports/{site_a.id}/r.pdf"
    (tmp_path / "reports" / str(site_a.id)).mkdir(parents=True)
    (tmp_path / rel).write_bytes(pdf)
    report = MonthlyReport(website_id=site_a.id, period=PERIOD, kind="client", status="ready", approved_for_client=True,
                           file_path=rel, file_sha256=hashlib.sha256(pdf).hexdigest(), size_bytes=len(pdf),
                           generated_at=now, data_snapshot={"status": {"level": "healthy"}})
    maint = MaintenanceActivity(website_id=site_a.id, performed_at=now, title="Updated plugins")
    rule = NotificationRule(event_type="website_down", recipients=["ops@example.com"])
    integ = HostingIntegration(provider_key="x", display_name="x", encrypted_credentials=b"x")
    log = NotificationLog(website_id=site_a.id, event_type="website_down", dedup_key="k", channel="email",
                          recipients=["ops@example.com"], status="sent")
    db.add_all([report, maint, rule, integ, log])
    db.commit()
    return {
        "actors": actors, "client_a": ca, "site_a": site_a,
        "ids": {"website_id": site_a.id, "incident_id": incident.id, "issue_id": issue.id, "run_id": run.id,
                "report_id": report.id, "activity_id": maint.id, "rule_id": rule.id, "integration_id": integ.id,
                "user_id": target_user.id, "client_id": ca.id},
        # identifiers of tenant A's data that must never reach out-of-scope actors
        "tenant_a_secrets": [str(x) for x in (site_a.id, incident.id, issue.id, run.id, report.id, maint.id)]
                            + ["Site A", "a.example.com", "Updated plugins"],
    }


def session_client(db, user: User | None) -> ApiClient:
    c = ApiClient(app)
    if user is None:
        return c
    token, csrf = new_token(), new_token()
    db.add(UserSession(token_hash=hash_token(token), csrf_token=csrf, user_id=user.id,
                       expires_at=datetime.now(UTC) + timedelta(hours=1)))
    db.commit()
    c.cookies.set("sw_session", token)
    c.csrf = csrf
    return c


def call(c: ApiClient, method: str, path: str, w: dict, kwargs_factory):
    url = path.format(**{k: str(v) for k, v in w["ids"].items()})
    kwargs = kwargs_factory(w) if kwargs_factory else {}
    return c.request(method, url, **kwargs)


# ---- tests --------------------------------------------------------------------------------------

def test_every_route_is_classified():
    live = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    assert live - set(ROUTES) == set(), "unclassified routes: add them to the permission matrix"
    assert set(ROUTES) - live == set(), "matrix lists routes that no longer exist"


CASES = [(m, p, a) for (m, p) in sorted(ROUTES) for a in ACTORS]


@pytest.mark.parametrize("method, path, actor", CASES, ids=[f"{m} {p} [{a}]" for m, p, a in CASES])
def test_authorization_matrix(world, db, method, path, actor):
    perms, scoped, kwargs = ROUTES[(method, path)]
    c = session_client(db, None if actor == "anon" else world["actors"][actor])
    r = call(c, method, path, world, kwargs)
    assert r.status_code < 500, f"server error: {r.text[:300]}"

    if perms == PUBLIC:
        expected = "allowed"
    elif perms == "login":
        expected = 401  # bad credentials are refused the same way for everyone
    elif actor == "anon":
        expected = 401
    elif not actor_may(actor, perms):
        expected = 403
    elif scoped and actor not in IN_SCOPE_A:
        expected = 404
    else:
        expected = "allowed"

    if expected == "allowed":
        assert r.status_code not in (401, 403, 404), f"{actor} should be allowed: {r.status_code} {r.text[:200]}"
    else:
        assert r.status_code == expected, f"{actor}: expected {expected}, got {r.status_code} {r.text[:200]}"

    # Isolation: actors outside tenant A never receive tenant A's data, from any endpoint.
    if actor in ("client_b", "dev_unassigned") and r.status_code < 400 and perms not in (PUBLIC, "login"):
        body = r.text
        leaked = [s for s in world["tenant_a_secrets"] if s in body]
        assert not leaked, f"{actor} received tenant A data {leaked} from {method} {path}"


MUTATIONS = sorted(k for k in ROUTES if k[0] != "GET" and ROUTES[k][0] not in (PUBLIC, "login"))


@pytest.mark.parametrize("method, path", MUTATIONS, ids=[f"{m} {p}" for m, p in MUTATIONS])
def test_state_changes_require_csrf(world, db, method, path):
    c = session_client(db, world["actors"]["admin"])
    c.csrf = None  # session cookie only, as a cross-site forged request would have
    r = call(c, method, path, world, ROUTES[(method, path)][2])
    assert r.status_code == 403 and "CSRF" in r.text


def test_client_users_get_client_shaped_payloads(world, db):
    """Allowed responses for client users must not contain staff-only data."""
    c = session_client(db, world["actors"]["client_a"])
    ids = world["ids"]
    forbidden_fields = ["cause_summary", "error_detail", "crawl_config", "notification_recipients", "assigned_users",
                        "account_reference", "internal_notes", "comment_count", "resolution_note", "emailed_to\": [\"",
                        "\"lookup\"", "first_check_id"]
    for path in ("/api/v1/websites", f"/api/v1/websites/{ids['website_id']}", "/api/v1/incidents",
                 f"/api/v1/incidents/{ids['incident_id']}", "/api/v1/issues", f"/api/v1/issues/{ids['issue_id']}",
                 "/api/v1/dashboard/summary", f"/api/v1/websites/{ids['website_id']}/expiry", "/api/v1/expiry/overview",
                 "/api/v1/reports", f"/api/v1/reports/{ids['report_id']}"):
        r = c.get(path)
        assert r.status_code == 200, path
        hits = [f for f in forbidden_fields if f in r.text]
        assert not hits, f"{path} exposes {hits} to a client user"
    assert "internal" not in c.get(f"/api/v1/issues/{ids['issue_id']}").text
