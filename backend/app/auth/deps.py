"""FastAPI dependencies for authentication, CSRF protection and permission checks."""
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_token
from app.config import get_settings
from app.db import get_db
from app.models import Client, User, UserSession

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    # Behind the reverse proxy (Caddy), uvicorn is started with --proxy-headers and a
    # trusted forwarded-allow-ips, so request.client already reflects the real client.
    return request.client.host if request.client else "unknown"


def get_current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if not token:
        raise unauthorized
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    now = datetime.now(UTC)
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now or not sess.user.is_active:
        raise unauthorized
    if sess.user.client_id is not None:
        # Users of a deactivated client organization lose access immediately.
        client = db.get(Client, sess.user.client_id)
        if client is None or not client.is_active:
            raise unauthorized
    if request.method not in SAFE_METHODS:
        header = request.headers.get("X-CSRF-Token", "")
        if not header or not secrets.compare_digest(header, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    request.state.session = sess
    return sess


def get_current_user(sess: UserSession = Depends(get_current_session)) -> User:
    return sess.user


def require(*perms: str) -> Callable[..., User]:
    """Dependency factory: user must hold ALL listed permissions."""

    def checker(user: User = Depends(get_current_user)) -> User:
        missing = set(perms) - user.permission_keys
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return checker


def require_any(*perms: str) -> Callable[..., User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if not (set(perms) & user.permission_keys):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return checker
