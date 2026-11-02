"""Login/logout logic with rate limiting, account lockout and session management."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.ratelimit import RateLimiter
from app.auth.security import hash_password, hash_token, needs_rehash, new_token, verify_password
from app.config import get_settings
from app.models import Client, User, UserSession
from app.services.audit_log import record


class LoginError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class IssuedSession:
    token: str
    csrf_token: str
    session: UserSession


GENERIC_FAILURE = "Invalid email or password."


def login(db: Session, limiter: RateLimiter, email: str, password: str, ip: str, user_agent: str) -> IssuedSession:
    s = get_settings()
    email = email.strip().lower()
    ip_bucket, email_bucket = f"login:ip:{ip}", f"login:email:{email}"

    ip_ok = limiter.hit(ip_bucket, s.login_max_attempts_per_ip, s.login_window_seconds)
    email_ok = limiter.hit(email_bucket, s.login_max_attempts_per_email, s.login_window_seconds)
    if not (ip_ok and email_ok):
        raise LoginError("Too many login attempts. Please wait and try again.", 429)

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    now = datetime.now(UTC)

    if user is not None and user.locked_until and user.locked_until > now:
        verify_password(None, password)  # keep timing similar
        raise LoginError("Account temporarily locked due to repeated failed logins.", 423)

    if user is None or not verify_password(user.password_hash, password) or not user.is_active:
        if user is not None and user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= s.account_lockout_threshold:
                user.locked_until = now + timedelta(minutes=s.account_lockout_minutes)
                user.failed_login_count = 0
                record(db, user.id, "auth.lockout", "user", user.id, ip)
            db.commit()
        raise LoginError(GENERIC_FAILURE)

    if user.client_id is not None:
        client = db.get(Client, user.client_id)
        if client is None or not client.is_active:
            raise LoginError(GENERIC_FAILURE)

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    limiter.reset(email_bucket)

    token, csrf = new_token(), new_token()
    sess = UserSession(
        token_hash=hash_token(token),
        csrf_token=csrf,
        user_id=user.id,
        expires_at=now + timedelta(hours=s.session_ttl_hours),
        ip=ip,
        user_agent=(user_agent or "")[:400],
    )
    db.add(sess)
    record(db, user.id, "auth.login", "user", user.id, ip)
    db.commit()
    return IssuedSession(token=token, csrf_token=csrf, session=sess)


def revoke_session(db: Session, sess: UserSession) -> None:
    sess.revoked_at = datetime.now(UTC)
    db.commit()


def revoke_all_sessions(db: Session, user_id, except_id=None) -> None:
    stmt = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_id is not None:
        stmt = stmt.where(UserSession.id != except_id)
    db.execute(stmt.values(revoked_at=datetime.now(UTC)))
