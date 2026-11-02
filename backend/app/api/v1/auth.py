from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, get_current_session
from app.auth.ratelimit import RateLimiter, get_redis
from app.auth.security import hash_password, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import UserSession
from app.schemas.users import ChangePasswordIn, LoginIn, MeOut
from app.services import auth_service
from app.services.audit_log import record
from app.services.user_service import check_password

router = APIRouter(prefix="/auth", tags=["auth"])


def get_limiter() -> RateLimiter:
    return RateLimiter(get_redis())


def _me(sess: UserSession) -> MeOut:
    u = sess.user
    return MeOut(
        id=u.id, email=u.email, full_name=u.full_name, roles=sorted(u.role_keys),
        permissions=sorted(u.permission_keys), client_id=u.client_id, csrf_token=sess.csrf_token,
    )


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db),
          limiter: RateLimiter = Depends(get_limiter)):
    s = get_settings()
    try:
        issued = auth_service.login(db, limiter, body.email, body.password, client_ip(request),
                                    request.headers.get("user-agent", ""))
    except auth_service.LoginError as e:
        raise HTTPException(e.status_code, e.message) from None
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(s.session_cookie_name, issued.token, max_age=max_age, httponly=True,
                        secure=s.cookie_secure, samesite="lax", path="/")
    # Readable by the frontend JS so it can echo it in the X-CSRF-Token header.
    response.set_cookie(s.csrf_cookie_name, issued.csrf_token, max_age=max_age, httponly=False,
                        secure=s.cookie_secure, samesite="lax", path="/")
    return _me(issued.session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, sess: UserSession = Depends(get_current_session), db: Session = Depends(get_db)):
    s = get_settings()
    auth_service.revoke_session(db, sess)
    response.delete_cookie(s.session_cookie_name, path="/")
    response.delete_cookie(s.csrf_cookie_name, path="/")


@router.get("/me", response_model=MeOut)
def me(sess: UserSession = Depends(get_current_session)):
    return _me(sess)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: ChangePasswordIn, request: Request, sess: UserSession = Depends(get_current_session),
                    db: Session = Depends(get_db)):
    user = sess.user
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    check_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    auth_service.revoke_all_sessions(db, user.id, except_id=sess.id)
    record(db, user.id, "auth.password_changed", "user", user.id, client_ip(request))
    db.commit()
