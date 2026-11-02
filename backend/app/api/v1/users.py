import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.auth.permissions import SUPER_ADMIN
from app.auth.security import hash_password
from app.db import get_db
from app.models import User
from app.schemas.users import RolesUpdate, UserCreate, UserOut, UserUpdate
from app.services import auth_service
from app.services.audit_log import record
from app.services.user_service import apply_role, check_password, create_user, ensure_other_active_admin, to_out

router = APIRouter(prefix="/users", tags=["users"])
Admin = Depends(require("user.manage"))


def _get(db: Session, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return u


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Admin):
    return [to_out(u) for u in db.scalars(select(User).order_by(User.email))]


@router.post("", response_model=UserOut, status_code=201)
def add_user(body: UserCreate, request: Request, db: Session = Depends(get_db), actor: User = Admin):
    u = create_user(db, body.email, body.password, body.role, body.full_name, body.client_id)
    record(db, actor.id, "user.create", "user", u.id, client_ip(request), role=body.role)
    db.commit()
    return to_out(u)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, db: Session = Depends(get_db), _: User = Admin):
    return to_out(_get(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, db: Session = Depends(get_db),
                actor: User = Admin):
    u = _get(db, user_id)
    if body.email is not None and body.email.lower() != u.email:
        if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
            raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists.")
        u.email = body.email.lower()
    if body.full_name is not None:
        u.full_name = body.full_name
    if body.password is not None:
        check_password(body.password)
        u.password_hash = hash_password(body.password)
        auth_service.revoke_all_sessions(db, u.id)
    record(db, actor.id, "user.update", "user", u.id, client_ip(request),
           fields=sorted(body.model_dump(exclude_none=True, exclude={"password"}).keys())
           + (["password"] if body.password else []))
    db.commit()
    return to_out(u)


@router.put("/{user_id}/role", response_model=UserOut)
def set_role(user_id: uuid.UUID, body: RolesUpdate, request: Request, db: Session = Depends(get_db),
             actor: User = Admin):
    u = _get(db, user_id)
    apply_role(db, u, body.role, body.client_id)
    auth_service.revoke_all_sessions(db, u.id)  # force re-login with new permissions
    record(db, actor.id, "user.role", "user", u.id, client_ip(request), role=body.role)
    db.commit()
    return to_out(u)


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db), actor: User = Admin):
    u = _get(db, user_id)
    if u.id == actor.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate your own account.")
    if SUPER_ADMIN in u.role_keys:
        ensure_other_active_admin(db, u)
    u.is_active = False
    auth_service.revoke_all_sessions(db, u.id)
    record(db, actor.id, "user.deactivate", "user", u.id, client_ip(request))
    db.commit()
    return to_out(u)


@router.post("/{user_id}/activate", response_model=UserOut)
def activate(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db), actor: User = Admin):
    u = _get(db, user_id)
    u.is_active = True
    u.locked_until = None
    u.failed_login_count = 0
    record(db, actor.id, "user.activate", "user", u.id, client_ip(request))
    db.commit()
    return to_out(u)
