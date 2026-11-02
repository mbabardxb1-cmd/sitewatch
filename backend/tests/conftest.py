"""Test fixtures. Tests run against a real PostgreSQL database and Redis (db 15).

Env: TEST_DATABASE_URL (default: local sitewatch_test), TEST_REDIS_URL.
"""
import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sitewatch:sitewatch@localhost:5432/sitewatch_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["COOKIE_SECURE"] = "false"  # TestClient talks plain http
os.environ["ENV"] = "test"

import uuid  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth.ratelimit import get_redis  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, Website  # noqa: E402
from app.services.user_service import create_user  # noqa: E402

PASSWORD = "Correct-Horse-9-battery"
KEEP_TABLES = {"roles", "permissions", "role_permissions", "system_settings", "alembic_version"}
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_state():
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))]
        wipe = [t for t in tables if t not in KEEP_TABLES]
        if wipe:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in wipe) + " RESTART IDENTITY CASCADE"))
    get_redis().flushdb()
    yield


@pytest.fixture
def settings():
    """Mutable settings; changes are reverted after the test."""
    s = get_settings()
    snapshot = s.model_dump()
    yield s
    for k, v in snapshot.items():
        setattr(s, k, v)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def make_client(db):
    def _make(name: str = "Acme", show_expiry: bool = False) -> Client:
        c = Client(name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", show_expiry_to_client=show_expiry)
        db.add(c)
        db.commit()
        return c

    return _make


@pytest.fixture
def make_website(db):
    def _make(client: Client, name: str = "Site", url: str = "https://example.com", assign=()) -> Website:
        w = Website(client_id=client.id, name=name, base_url=url, hostname=url.split("//")[1].split("/")[0])
        w.assigned_users = list(assign)
        db.add(w)
        db.commit()
        return w

    return _make


@pytest.fixture
def make_user(db):
    def _make(role: str = "developer", client: Client | None = None, email: str | None = None):
        u = create_user(db, email or f"{role}-{uuid.uuid4().hex[:8]}@example.com", PASSWORD, role,
                        "Test User", client.id if client else None)
        db.commit()
        return u

    return _make


class ApiClient(TestClient):
    """TestClient that logs in and automatically sends the CSRF header."""

    csrf: str | None = None

    def login(self, email: str, password: str = PASSWORD):
        r = self.post("/api/v1/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def request(self, method, url, **kw):
        if self.csrf and method.upper() not in ("GET", "HEAD", "OPTIONS"):
            headers = dict(kw.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", self.csrf)
            kw["headers"] = headers
        return super().request(method, url, **kw)


@pytest.fixture
def api():
    return ApiClient(app)


@pytest.fixture
def as_user(make_user):
    """Return a logged-in ApiClient for a fresh user of the given role."""

    def _as(role: str = "developer", client: Client | None = None):
        u = make_user(role, client)
        c = ApiClient(app)
        assert c.login(u.email).status_code == 200
        c.user = u
        return c

    return _as
