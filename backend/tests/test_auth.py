from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AuditLog, UserSession
from tests.conftest import PASSWORD, ApiClient, app


def test_login_sets_secure_cookies_and_returns_permissions(api, make_user):
    u = make_user("developer")
    r = api.login(u.email)
    assert r.status_code == 200
    body = r.json()
    assert body["roles"] == ["developer"]
    assert "audit.run" in body["permissions"] and "user.manage" not in body["permissions"]
    cookies = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("sw_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    csrf_cookie = next(c for c in cookies if c.startswith("sw_csrf="))
    assert "HttpOnly" not in csrf_cookie
    assert api.get("/api/v1/auth/me").json()["email"] == u.email


def test_session_token_is_stored_hashed(api, make_user, db):
    u = make_user()
    api.login(u.email)
    raw = api.cookies.get("sw_session")
    stored = db.scalar(select(UserSession.token_hash).where(UserSession.user_id == u.id))
    assert raw and stored and raw != stored and len(stored) == 64


def test_wrong_password_and_unknown_user_give_same_error(api, make_user):
    u = make_user()
    r1 = api.login(u.email, "Wrong-password-123")
    r2 = api.login("nobody@example.com", "Wrong-password-123")
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()


def test_email_is_case_insensitive(api, make_user):
    u = make_user()
    assert api.login(u.email.upper()).status_code == 200


def test_inactive_user_cannot_login(api, make_user, db):
    u = make_user()
    u.is_active = False
    db.commit()
    assert api.login(u.email).status_code == 401


def test_unauthenticated_requests_rejected(api):
    assert api.get("/api/v1/auth/me").status_code == 401
    assert api.get("/api/v1/users").status_code == 401


def test_login_rate_limited_per_email(api, make_user, settings):
    u = make_user()
    for _ in range(settings.login_max_attempts_per_email):
        assert api.login(u.email, "Wrong-password-123").status_code == 401
    # Even the correct password is refused once the window is exhausted.
    assert api.login(u.email).status_code == 429


def test_login_rate_limited_per_ip(api, make_user, settings):
    settings.login_max_attempts_per_ip = 3
    for i in range(3):
        api.login(f"user{i}@example.com", "x")
    assert api.login("other@example.com", "x").status_code == 429


def test_account_lockout(api, make_user, settings, db):
    settings.account_lockout_threshold = 3
    settings.login_max_attempts_per_email = 100
    u = make_user()
    for _ in range(3):
        api.login(u.email, "Wrong-password-123")
    assert api.login(u.email).status_code == 423
    assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.lockout")) is not None


def test_csrf_required_for_state_changing_requests(api, make_user):
    u = make_user()
    api.login(u.email)
    token = api.csrf
    api.csrf = None  # stop auto-sending header
    assert api.post("/api/v1/auth/logout").status_code == 403
    assert api.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert api.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token}).status_code == 204


def test_logout_revokes_session(api, make_user):
    u = make_user()
    api.login(u.email)
    stolen = api.cookies.get("sw_session")
    assert api.post("/api/v1/auth/logout").status_code == 204
    replay = ApiClient(app)
    replay.cookies.set("sw_session", stolen)
    assert replay.get("/api/v1/auth/me").status_code == 401


def test_expired_session_rejected(api, make_user, db):
    u = make_user()
    api.login(u.email)
    sess = db.scalar(select(UserSession).where(UserSession.user_id == u.id))
    sess.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert api.get("/api/v1/auth/me").status_code == 401


def test_change_password_revokes_other_sessions(make_user):
    u = make_user()
    a, b = ApiClient(app), ApiClient(app)
    a.login(u.email)
    b.login(u.email)
    r = a.post("/api/v1/auth/change-password",
               json={"current_password": PASSWORD, "new_password": "New-Strong-pass-42"})
    assert r.status_code == 204
    assert a.get("/api/v1/auth/me").status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401
    assert ApiClient(app).login(u.email, "New-Strong-pass-42").status_code == 200


def test_change_password_rejects_weak_and_wrong_current(as_user):
    c = as_user()
    assert c.post("/api/v1/auth/change-password",
                  json={"current_password": "nope", "new_password": "New-Strong-pass-42"}).status_code == 400
    assert c.post("/api/v1/auth/change-password",
                  json={"current_password": PASSWORD, "new_password": "short"}).status_code == 422


def test_security_headers_and_health(api):
    r = api.get("/api/healthz")
    assert r.status_code == 200
    assert r.headers["X-Frame-Options"] == "DENY"
    ready = api.get("/api/readyz").json()
    assert ready["checks"] == {"database": "ok", "redis": "ok"}
