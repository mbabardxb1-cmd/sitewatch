import uuid

import pytest
from sqlalchemy import select

from app.models import AuditLog, UptimeCheck, UptimeIncident, Website

STAFF_ONLY_FIELDS = {"crawl_config", "notification_recipients", "assigned_users", "check_interval_s",
                     "expected_status_codes", "hostname", "retry_attempts", "timeout_s"}


def site_payload(client, **kw):
    return {"client_id": str(client.id), "name": "Main site", "base_url": "https://acme.example.com", **kw}


# ---- Clients -----------------------------------------------------------------------------

def test_client_management_permissions(as_user, make_client):
    admin, dev = as_user("super_admin"), as_user("developer")
    acme = make_client("Acme")
    client_user = as_user("client", acme)

    r = admin.post("/api/v1/clients", json={"name": "Globex", "slug": "globex", "show_expiry_to_client": True})
    assert r.status_code == 201 and r.json()["slug"] == "globex"
    assert admin.post("/api/v1/clients", json={"name": "G2", "slug": "globex"}).status_code == 409
    assert admin.post("/api/v1/clients", json={"name": "Bad", "slug": "Bad Slug!"}).status_code == 422

    assert dev.get("/api/v1/clients").status_code == 200  # developers may list clients
    assert dev.post("/api/v1/clients", json={"name": "X", "slug": "x"}).status_code == 403
    assert dev.patch(f"/api/v1/clients/{acme.id}", json={"name": "Hacked"}).status_code == 403
    assert client_user.get("/api/v1/clients").status_code == 403
    assert client_user.get(f"/api/v1/clients/{acme.id}").status_code == 403

    r = admin.patch(f"/api/v1/clients/{acme.id}", json={"show_expiry_to_client": True})
    assert r.status_code == 200 and r.json()["show_expiry_to_client"] is True


# ---- Website registration -------------------------------------------------------------

def test_developer_registers_website_and_is_auto_assigned(as_user, make_client, db):
    acme = make_client("Acme")
    dev = as_user("developer")
    r = dev.post("/api/v1/websites", json=site_payload(acme, check_interval_s=120, retry_attempts=3,
                                                       notification_recipients=["ops@acme.example.com"]))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["base_url"] == "https://acme.example.com/"
    assert body["check_interval_s"] == 120 and body["retry_attempts"] == 3
    assert body["health"] == "unknown" and body["health_reason"] == "No completed checks yet"
    assert body["next_check_at"] is not None  # will be picked up by the scheduler in phase 3
    assert [u["email"] for u in body["assigned_users"]] == [dev.user.email]
    assert body["crawl_config"]["max_pages"] == 500
    assert dev.get(f"/api/v1/websites/{body['id']}").status_code == 200
    assert db.scalar(select(AuditLog).where(AuditLog.action == "website.create")) is not None


@pytest.mark.parametrize("override, fragment", [
    ({"base_url": "http://localhost:8000"}, "Internal hostnames"),
    ({"base_url": "http://10.1.2.3"}, "Private"),
    ({"base_url": "ftp://acme.example.com"}, "http and https"),
    ({"check_interval_s": 30}, None),
    ({"check_interval_s": 100000}, None),
    ({"audit_day_of_month": 31}, None),
    ({"expected_status_codes": [999]}, None),
    ({"notification_recipients": ["not-an-email"]}, None),
    ({"crawl_config": {"max_pages": 0}}, None),
    ({"crawl_config": {"rate_rps": 100}}, None),
    ({"crawl_config": {"excluded_patterns": ["re:(unclosed"]}}, None),
    ({"crawl_config": {"unknown_option": 1}}, None),
])
def test_website_validation(as_user, make_client, override, fragment):
    acme = make_client("Acme")
    admin = as_user("super_admin")
    r = admin.post("/api/v1/websites", json=site_payload(acme, **override))
    assert r.status_code == 422, r.text
    if fragment:
        assert fragment in r.json()["detail"]


def test_duplicate_url_per_client_rejected_but_allowed_after_archive(as_user, make_client):
    acme = make_client("Acme")
    admin = as_user("super_admin")
    first = admin.post("/api/v1/websites", json=site_payload(acme)).json()
    assert admin.post("/api/v1/websites", json=site_payload(acme, base_url="acme.example.com")).status_code == 409
    admin.post(f"/api/v1/websites/{first['id']}/archive")
    second = admin.post("/api/v1/websites", json=site_payload(acme, base_url="acme.example.com"))
    assert second.status_code == 201
    # Restoring the archived duplicate now conflicts
    assert admin.post(f"/api/v1/websites/{first['id']}/restore").status_code == 409


def test_cannot_create_for_unknown_or_inactive_client(as_user, make_client, db):
    admin = as_user("super_admin")
    fake = type("C", (), {"id": uuid.uuid4()})
    assert admin.post("/api/v1/websites", json=site_payload(fake)).status_code == 422
    old = make_client("Old")
    old.is_active = False
    db.commit()
    assert admin.post("/api/v1/websites", json=site_payload(old)).status_code == 422


def test_client_users_cannot_write(as_user, make_client, make_website):
    acme = make_client("Acme")
    cu = as_user("client", acme)
    site = make_website(acme, assign=[cu.user])
    assert cu.post("/api/v1/websites", json=site_payload(acme)).status_code == 403
    assert cu.patch(f"/api/v1/websites/{site.id}", json={"name": "x"}).status_code == 403
    assert cu.put(f"/api/v1/websites/{site.id}/crawl-config", json={}).status_code == 403
    assert cu.post(f"/api/v1/websites/{site.id}/archive").status_code == 403


# ---- Editing, archiving, crawl config -------------------------------------------------

def test_update_monitoring_settings_and_url(as_user, make_client, db):
    acme = make_client("Acme")
    admin = as_user("super_admin")
    site_id = admin.post("/api/v1/websites", json=site_payload(acme)).json()["id"]
    site = db.get(Website, uuid.UUID(site_id))
    site.current_state = "up"
    db.commit()

    r = admin.patch(f"/api/v1/websites/{site_id}", json={"check_interval_s": 60, "status": "paused",
                                                          "expected_status_codes": [200, 301]})
    assert r.status_code == 200
    assert r.json()["status"] == "paused" and r.json()["health_reason"] == "Monitoring paused"
    r = admin.patch(f"/api/v1/websites/{site_id}", json={"base_url": "https://www.acme.example.com/"})
    assert r.json()["current_state"] == "unknown"  # old results no longer apply to the new URL
    assert admin.patch(f"/api/v1/websites/{site_id}", json={"base_url": "http://192.168.0.1"}).status_code == 422
    assert admin.patch(f"/api/v1/websites/{site_id}", json={"name": None}).status_code == 422
    assert admin.patch(f"/api/v1/websites/{site_id}", json={"status": "archived"}).status_code == 422


def test_archive_keeps_record_and_blocks_edits(as_user, make_client):
    acme = make_client("Acme")
    admin = as_user("super_admin")
    site_id = admin.post("/api/v1/websites", json=site_payload(acme)).json()["id"]
    r = admin.post(f"/api/v1/websites/{site_id}/archive").json()
    assert r["status"] == "archived" and r["next_check_at"] is None and r["archived_at"]
    assert admin.patch(f"/api/v1/websites/{site_id}", json={"name": "x"}).status_code == 409
    assert [s["id"] for s in admin.get("/api/v1/websites?status=archived").json()] == [site_id]
    assert admin.get("/api/v1/websites?status=active").json() == []
    restored = admin.post(f"/api/v1/websites/{site_id}/restore").json()
    assert restored["status"] == "paused" and restored["archived_at"] is None


def test_crawl_config_update(as_user, make_client):
    acme = make_client("Acme")
    dev = as_user("developer")
    site_id = dev.post("/api/v1/websites", json=site_payload(acme)).json()["id"]
    cfg = {"max_depth": 5, "max_pages": 2000, "timeout_s": 20, "rate_rps": 1.5,
           "excluded_patterns": ["/wp-admin/*", "re:\\?replytocom=", "  "], "check_external_links": False,
           "render_js": False, "slow_page_ms": 4000}
    r = dev.put(f"/api/v1/websites/{site_id}/crawl-config", json=cfg)
    assert r.status_code == 200
    assert r.json()["crawl_config"]["excluded_patterns"] == ["/wp-admin/*", "re:\\?replytocom="]
    assert r.json()["crawl_config"]["max_pages"] == 2000


# ---- Assignments ----------------------------------------------------------------------

def test_assignments_admin_only_and_tenant_checked(as_user, make_client, make_user):
    acme, globex = make_client("Acme"), make_client("Globex")
    admin, dev = as_user("super_admin"), as_user("developer")
    site_id = admin.post("/api/v1/websites", json=site_payload(acme)).json()["id"]
    acme_user, globex_user = make_user("client", acme), make_user("client", globex)

    assert dev.put(f"/api/v1/websites/{site_id}/assignments", json={"user_ids": []}).status_code == 403
    r = admin.put(f"/api/v1/websites/{site_id}/assignments", json={"user_ids": [str(globex_user.id)]})
    assert r.status_code == 422 and "different client" in r.json()["detail"]
    r = admin.put(f"/api/v1/websites/{site_id}/assignments", json={"user_ids": [str(uuid.uuid4())]})
    assert r.status_code == 422
    r = admin.put(f"/api/v1/websites/{site_id}/assignments",
                  json={"user_ids": [str(acme_user.id), str(dev.user.id)]})
    assert r.status_code == 200
    assert {u["email"] for u in r.json()["assigned_users"]} == {acme_user.email, dev.user.email}
    assert dev.get(f"/api/v1/websites/{site_id}").status_code == 200


# ---- Tenant isolation & data shaping ----------------------------------------------------

@pytest.fixture
def two_tenants(make_client, make_website, as_user):
    acme, globex = make_client("Acme"), make_client("Globex")
    acme_cu, globex_cu, dev = as_user("client", acme), as_user("client", globex), as_user("developer")
    a1 = make_website(acme, "A1", "https://a1.example.com", assign=[acme_cu.user, dev.user])
    a2 = make_website(acme, "A2", "https://a2.example.com")
    g1 = make_website(globex, "G1", "https://g1.example.com", assign=[globex_cu.user])
    return dict(acme_cu=acme_cu, globex_cu=globex_cu, dev=dev, a1=a1, a2=a2, g1=g1)


def test_list_is_scoped(two_tenants):
    t = two_tenants
    assert [s["name"] for s in t["acme_cu"].get("/api/v1/websites").json()] == ["A1"]
    assert [s["name"] for s in t["globex_cu"].get("/api/v1/websites").json()] == ["G1"]
    assert [s["name"] for s in t["dev"].get("/api/v1/websites").json()] == ["A1"]
    # Filtering by another client's id yields nothing rather than leaking
    assert t["acme_cu"].get(f"/api/v1/websites?client_id={t['g1'].client_id}").json() == []


@pytest.mark.parametrize("who, site", [("acme_cu", "g1"), ("acme_cu", "a2"), ("globex_cu", "a1"), ("dev", "g1")])
def test_out_of_scope_access_is_indistinguishable_from_missing(two_tenants, who, site):
    c, target = two_tenants[who], two_tenants[site]
    missing = c.get(f"/api/v1/websites/{uuid.uuid4()}")
    forbidden = c.get(f"/api/v1/websites/{target.id}")
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


@pytest.mark.parametrize("method, path, body", [
    ("patch", "", {"name": "pwned"}),
    ("put", "/crawl-config", {}),
    ("post", "/archive", None),
    ("post", "/restore", None),
])
def test_developer_cannot_modify_unassigned_site(two_tenants, method, path, body):
    t = two_tenants
    kw = {"json": body} if body is not None else {}
    r = getattr(t["dev"], method)(f"/api/v1/websites/{t['g1'].id}{path}", **kw)
    assert r.status_code == 404


def test_client_view_hides_technical_fields(two_tenants):
    t = two_tenants
    one = t["acme_cu"].get(f"/api/v1/websites/{t['a1'].id}").json()
    listed = t["acme_cu"].get("/api/v1/websites").json()[0]
    dash = t["acme_cu"].get("/api/v1/dashboard/summary").json()["websites"][0]
    for payload in (one, listed, dash):
        assert not (STAFF_ONLY_FIELDS & payload.keys()), payload.keys()
        assert payload["health"] == "unknown"
    staff = t["dev"].get(f"/api/v1/websites/{t['a1'].id}").json()
    assert STAFF_ONLY_FIELDS <= staff.keys()


def test_client_does_not_see_archived_sites(two_tenants, db):
    t = two_tenants
    t["a1"].status = "archived"
    db.commit()
    assert t["acme_cu"].get("/api/v1/websites").json() == []


# ---- Dashboard ------------------------------------------------------------------------

def test_dashboard_summary_counts_and_no_fabricated_values(as_user, make_client, make_website, db):
    acme, globex = make_client("Acme"), make_client("Globex")
    dev = as_user("developer")
    up = make_website(acme, "Up", "https://up.example.com", assign=[dev.user])
    down = make_website(acme, "Down", "https://down.example.com", assign=[dev.user])
    slow = make_website(acme, "Slow", "https://slow.example.com", assign=[dev.user])
    paused = make_website(acme, "Paused", "https://p.example.com", assign=[dev.user])
    hidden = make_website(globex, "Hidden", "https://h.example.com")  # not assigned
    up.current_state, down.current_state, slow.current_state = "up", "down", "degraded"
    paused.status = "paused"
    db.add_all([
        UptimeCheck(website_id=up.id, ok=True, http_status=200, response_ms=100),
        UptimeCheck(website_id=up.id, ok=True, http_status=200, response_ms=300),
        UptimeCheck(website_id=down.id, ok=False, error_type="timeout"),
        UptimeCheck(website_id=hidden.id, ok=True, http_status=200, response_ms=9000),  # out of scope
        UptimeIncident(website_id=down.id, started_at=up.created_at),
        UptimeIncident(website_id=hidden.id, started_at=up.created_at),  # out of scope
    ])
    db.commit()

    s = dev.get("/api/v1/dashboard/summary").json()
    assert (s["total_websites"], s["online"], s["offline"], s["degraded"], s["unknown"], s["paused"]) == (4, 1, 1, 1, 0, 1)
    assert s["avg_response_ms_24h"] == 200 and s["checks_24h"] == 2
    assert s["active_incidents"] == 1
    health = {w["name"]: w["health"] for w in s["websites"]}
    assert health == {"Up": "healthy", "Down": "critical", "Slow": "warning", "Paused": "unknown"}


def test_dashboard_empty_scope(as_user):
    s = as_user("developer").get("/api/v1/dashboard/summary").json()
    assert s["total_websites"] == 0 and s["avg_response_ms_24h"] is None and s["active_incidents"] == 0


def test_deactivating_client_locks_out_its_users(as_user, make_client):
    acme = make_client("Acme")
    admin, cu = as_user("super_admin"), as_user("client", acme)
    assert cu.get("/api/v1/auth/me").status_code == 200
    assert admin.patch(f"/api/v1/clients/{acme.id}", json={"is_active": False}).status_code == 200
    assert cu.get("/api/v1/auth/me").status_code == 401
    assert cu.get("/api/v1/websites").status_code == 401
    assert cu.login(cu.user.email).status_code == 401
