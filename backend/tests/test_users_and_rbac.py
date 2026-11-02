import pytest
from sqlalchemy import select

from app.auth.permissions import PERMISSIONS, ROLES
from app.auth.scope import get_website_or_404, visible_website_ids
from app.models import AuditLog, Role
from tests.conftest import ApiClient, app

STRONG = "Another-Strong-pw-7"


# ---- Role catalogue -------------------------------------------------------------------

def test_roles_seeded_match_catalogue(db):
    for key, (_, grants) in ROLES.items():
        role = db.scalar(select(Role).where(Role.key == key))
        assert {p.key for p in role.permissions} == grants
    assert ROLES["super_admin"][1] == set(PERMISSIONS)


@pytest.mark.parametrize("perm", [
    "issue.note.read", "uptime.read_detailed", "audit.read_detailed", "expiry.read_detailed",
    "report.read_all", "website.write", "user.manage", "settings.manage", "integration.manage",
])
def test_client_role_lacks_sensitive_permissions(perm):
    assert perm not in ROLES["client"][1]


@pytest.mark.parametrize("perm", ["user.manage", "settings.manage", "integration.manage", "website.all"])
def test_developer_lacks_admin_permissions(perm):
    assert perm not in ROLES["developer"][1]


# ---- User management API ---------------------------------------------------------------

@pytest.mark.parametrize("role", ["developer", "client"])
def test_non_admins_cannot_manage_users(as_user, make_client, role):
    c = as_user(role, make_client() if role == "client" else None)
    assert c.get("/api/v1/users").status_code == 403
    r = c.post("/api/v1/users", json={"email": "x@example.com", "password": STRONG, "role": "super_admin"})
    assert r.status_code == 403


def test_admin_creates_users_with_validation(as_user, make_client):
    admin = as_user("super_admin")
    acme = make_client("Acme")
    ok = admin.post("/api/v1/users", json={"email": "Dev@Example.com", "password": STRONG, "role": "developer"})
    assert ok.status_code == 201 and ok.json()["email"] == "dev@example.com"
    # duplicate
    assert admin.post("/api/v1/users", json={"email": "dev@example.com", "password": STRONG,
                                             "role": "developer"}).status_code == 409
    # weak password
    assert admin.post("/api/v1/users", json={"email": "w@example.com", "password": "weakpassword",
                                             "role": "developer"}).status_code == 422
    # client user requires a client org
    assert admin.post("/api/v1/users", json={"email": "c@example.com", "password": STRONG,
                                             "role": "client"}).status_code == 422
    # staff must not be linked to a client org
    assert admin.post("/api/v1/users", json={"email": "d2@example.com", "password": STRONG, "role": "developer",
                                             "client_id": str(acme.id)}).status_code == 422
    good_client = admin.post("/api/v1/users", json={"email": "c@example.com", "password": STRONG,
                                                    "role": "client", "client_id": str(acme.id)})
    assert good_client.status_code == 201 and good_client.json()["roles"] == ["client"]
    # invalid role value
    assert admin.post("/api/v1/users", json={"email": "r@example.com", "password": STRONG,
                                             "role": "root"}).status_code == 422


def test_deactivation_revokes_sessions_and_blocks_login(as_user, make_user, db):
    admin = as_user("super_admin")
    dev = make_user("developer")
    dev_client = ApiClient(app)
    dev_client.login(dev.email)
    assert admin.post(f"/api/v1/users/{dev.id}/deactivate").status_code == 200
    assert dev_client.get("/api/v1/auth/me").status_code == 401
    assert ApiClient(app).login(dev.email).status_code == 401
    assert db.scalar(select(AuditLog).where(AuditLog.action == "user.deactivate")) is not None


def test_admin_cannot_deactivate_self_or_last_admin(as_user):
    admin = as_user("super_admin")
    assert admin.post(f"/api/v1/users/{admin.user.id}/deactivate").status_code == 409
    # demoting the only admin is refused
    assert admin.put(f"/api/v1/users/{admin.user.id}/role", json={"role": "developer"}).status_code == 409


def test_role_change_takes_effect_and_revokes_sessions(as_user, make_user):
    admin = as_user("super_admin")
    u = make_user("developer")
    c = ApiClient(app)
    c.login(u.email)
    assert admin.put(f"/api/v1/users/{u.id}/role", json={"role": "super_admin"}).status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401
    c.login(u.email)
    assert c.get("/api/v1/users").status_code == 200


def test_unknown_user_id_is_404(as_user):
    admin = as_user("super_admin")
    assert admin.get("/api/v1/users/00000000-0000-0000-0000-000000000000").status_code == 404
    assert admin.get("/api/v1/users/not-a-uuid").status_code == 422


# ---- Tenant scoping --------------------------------------------------------------------

def test_website_scope_isolation(db, make_client, make_website, make_user):
    acme, globex = make_client("Acme"), make_client("Globex")
    dev = make_user("developer")
    acme_user = make_user("client", acme)
    admin = make_user("super_admin")

    a1 = make_website(acme, "A1", "https://a1.example", assign=[dev, acme_user])
    a2 = make_website(acme, "A2", "https://a2.example")  # unassigned
    g1 = make_website(globex, "G1", "https://g1.example", assign=[dev])
    # Mis-assignment: an Acme client user assigned to a Globex site must still see nothing of it.
    g2 = make_website(globex, "G2", "https://g2.example", assign=[acme_user])

    assert set(visible_website_ids(db, admin)) == {a1.id, a2.id, g1.id, g2.id}
    assert set(visible_website_ids(db, dev)) == {a1.id, g1.id}
    assert set(visible_website_ids(db, acme_user)) == {a1.id}

    from fastapi import HTTPException
    for site in (a2, g1, g2):
        with pytest.raises(HTTPException) as e:
            get_website_or_404(db, acme_user, site.id)
        assert e.value.status_code == 404
    assert get_website_or_404(db, acme_user, a1.id).id == a1.id
