"""Editing users: profile, role, per-user permissions and website access."""
import uuid

from app.models import User


def test_permission_catalogue_lists_every_permission(as_user):
    from app.auth.permissions import PERMISSIONS

    cat = as_user("super_admin").get("/api/v1/users/permission-catalogue").json()
    keys = [p["key"] for g in cat["groups"] for p in g["permissions"]]
    assert sorted(keys) == sorted(PERMISSIONS) and cat["customizable_roles"] == ["developer"]


def test_developer_permissions_can_be_added_and_removed(db, as_user, make_user):
    admin = as_user("super_admin")
    dev = as_user("developer")
    assert dev.get("/api/v1/users").status_code == 403
    base = set(dev.get("/api/v1/auth/me").json()["permissions"])
    wanted = (base - {"audit.run"}) | {"user.manage"}
    r = admin.put(f"/api/v1/users/{dev.user.id}/permissions", json={"permissions": sorted(wanted)})
    assert r.status_code == 200 and r.json()["custom_permissions"] is True
    assert set(r.json()["permissions"]) == wanted
    # takes effect immediately for the logged-in user
    assert set(dev.get("/api/v1/auth/me").json()["permissions"]) == wanted
    assert dev.get("/api/v1/users").status_code == 200
    # back to role defaults
    r = admin.put(f"/api/v1/users/{dev.user.id}/permissions", json={"permissions": sorted(base)})
    assert r.json()["custom_permissions"] is False
    assert admin.put(f"/api/v1/users/{dev.user.id}/permissions", json={"permissions": ["nope"]}).status_code == 422
    assert dev.put(f"/api/v1/users/{dev.user.id}/permissions", json={"permissions": sorted(wanted)}).status_code == 403


def test_permissions_of_admins_and_clients_are_fixed(as_user, make_client):
    admin = as_user("super_admin")
    other_admin = as_user("super_admin")
    client = as_user("client", make_client())
    for u in (other_admin, client):
        r = admin.put(f"/api/v1/users/{u.user.id}/permissions", json={"permissions": ["website.all"]})
        assert r.status_code == 409


def test_role_change_resets_custom_permissions(db, as_user):
    admin = as_user("super_admin")
    dev = as_user("developer")
    admin.put(f"/api/v1/users/{dev.user.id}/permissions", json={"permissions": ["website.read"]})
    r = admin.put(f"/api/v1/users/{dev.user.id}/role", json={"role": "super_admin"})
    assert r.status_code == 200 and r.json()["custom_permissions"] is False
    db.expire_all()
    assert not db.get(User, dev.user.id).permission_overrides


def test_profile_edit(as_user):
    admin = as_user("super_admin")
    dev = as_user("developer")
    r = admin.patch(f"/api/v1/users/{dev.user.id}", json={"full_name": "Hamza", "email": f"h-{uuid.uuid4().hex[:6]}@brande.ae"})
    assert r.status_code == 200 and r.json()["full_name"] == "Hamza" and r.json()["email"].endswith("@brande.ae")


def test_website_access_per_user(db, as_user, make_client, make_website):
    admin = as_user("super_admin")
    acme, globex = make_client("Acme"), make_client("Globex")
    a1, a2 = make_website(acme, "A1", "https://a1.example.com/"), make_website(acme, "A2", "https://a2.example.com/")
    g1 = make_website(globex, "G1", "https://g1.example.com/")
    dev, cu = as_user("developer"), as_user("client", acme)

    r = admin.put(f"/api/v1/users/{dev.user.id}/websites", json={"website_ids": [str(a1.id), str(g1.id)]})
    assert r.status_code == 200
    assert {w["name"] for w in r.json()["websites"] if w["assigned"]} == {"A1", "G1"}
    assert {w["name"] for w in dev.get("/api/v1/websites").json()} == {"A1", "G1"}

    # client users can only be given their own organization's websites
    assert {w["name"] for w in admin.get(f"/api/v1/users/{cu.user.id}/websites").json()["websites"]} == {"A1", "A2"}
    assert admin.put(f"/api/v1/users/{cu.user.id}/websites", json={"website_ids": [str(g1.id)]}).status_code == 422
    assert admin.put(f"/api/v1/users/{cu.user.id}/websites", json={"website_ids": [str(a2.id)]}).status_code == 200
    assert {w["name"] for w in cu.get("/api/v1/websites").json()} == {"A2"}

    # removing access
    admin.put(f"/api/v1/users/{dev.user.id}/websites", json={"website_ids": []})
    assert dev.get("/api/v1/websites").json() == []
    assert admin.get(f"/api/v1/users/{admin.user.id}/websites").json()["sees_all"] is True


def test_moving_a_client_user_to_another_org_drops_old_assignments(db, as_user, make_client, make_website):
    admin = as_user("super_admin")
    acme, globex = make_client("Acme"), make_client("Globex")
    a1 = make_website(acme, "A1", "https://a1.example.com/")
    cu = as_user("client", acme)
    admin.put(f"/api/v1/users/{cu.user.id}/websites", json={"website_ids": [str(a1.id)]})
    assert admin.put(f"/api/v1/users/{cu.user.id}/role", json={"role": "client", "client_id": str(globex.id)}).status_code == 200
    assert not any(w["assigned"] for w in admin.get(f"/api/v1/users/{cu.user.id}/websites").json()["websites"])
