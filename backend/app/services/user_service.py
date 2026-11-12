import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.permissions import CLIENT, SUPER_ADMIN
from app.auth.security import hash_password, validate_password_strength
from app.config import get_settings
from app.auth.permissions import CUSTOMIZABLE_ROLES
from app.models import Client, Permission, PermissionOverride, Role, User, Website, user_role_assignments
from app.schemas.users import UserOut


def to_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, email=u.email, full_name=u.full_name, is_active=u.is_active, client_id=u.client_id,
        roles=sorted(u.role_keys), last_login_at=u.last_login_at, created_at=u.created_at,
        permissions=sorted(u.permission_keys), custom_permissions=bool(u.permission_overrides),
    )


def set_permissions(db: Session, user: User, wanted: set[str]) -> None:
    """Store the difference between `wanted` and the role's permissions as per-user overrides."""
    if not (user.role_keys & CUSTOMIZABLE_ROLES):
        raise HTTPException(409, "Permissions can only be customised for Developer users. Super administrators "
                                 "always have every permission; client users have a fixed read-only set.")
    perms = {p.key: p for p in db.scalars(select(Permission))}
    unknown = sorted(wanted - set(perms))
    if unknown:
        raise HTTPException(422, f"Unknown permissions: {', '.join(unknown)}")
    base = user.role_permission_keys
    user.permission_overrides = (
        [PermissionOverride(user_id=user.id, permission_id=perms[k].id, granted=True) for k in sorted(wanted - base)]
        + [PermissionOverride(user_id=user.id, permission_id=perms[k].id, granted=False) for k in sorted(base - wanted)])
    db.flush()


def eligible_websites(db: Session, user: User) -> list[Website]:
    q = select(Website).where(Website.status != "archived").order_by(Website.name)
    if CLIENT in user.role_keys:
        q = q.where(Website.client_id == user.client_id)
    return list(db.scalars(q).unique())


def set_user_websites(db: Session, user: User, website_ids: set[uuid.UUID]) -> None:
    """Make `user` assigned to exactly these websites (archived ones are left as they are)."""
    eligible = {w.id: w for w in eligible_websites(db, user)}
    bad = website_ids - set(eligible)
    if bad:
        raise HTTPException(422, "One or more websites do not exist or belong to another client organization.")
    for w in eligible.values():
        assigned = any(u.id == user.id for u in w.assigned_users)
        if w.id in website_ids and not assigned:
            w.assigned_users.append(user)
        elif w.id not in website_ids and assigned:
            w.assigned_users = [u for u in w.assigned_users if u.id != user.id]
    db.flush()


def check_password(password: str) -> None:
    err = validate_password_strength(password, get_settings().password_min_length)
    if err:
        raise HTTPException(422, err)


def apply_role(db: Session, user: User, role_key: str, client_id: uuid.UUID | None) -> None:
    """Each user holds exactly one primary role. Client users must belong to a client;
    staff users must not."""
    if role_key == CLIENT:
        if client_id is None:
            raise HTTPException(422, "Client users must be linked to a client organization.")
        if db.get(Client, client_id) is None:
            raise HTTPException(422, "Client organization does not exist.")
    elif client_id is not None:
        raise HTTPException(422, "Only client users can be linked to a client organization.")

    if SUPER_ADMIN in user.role_keys and role_key != SUPER_ADMIN:
        ensure_other_active_admin(db, user)

    role = db.scalar(select(Role).where(Role.key == role_key))
    if role is None:
        raise HTTPException(500, "Roles not initialised; run migrations.")
    if user.role_keys != {role_key}:
        user.permission_overrides = []  # a new role starts from that role's defaults
    if role_key == CLIENT and user.id is not None:
        # a client user may only stay assigned to their own organization's websites
        for w in db.scalars(select(Website).where(Website.assigned_users.any(User.id == user.id))).unique():
            if w.client_id != client_id:
                w.assigned_users = [u for u in w.assigned_users if u.id != user.id]
    user.roles = [role]
    user.client_id = client_id


def ensure_other_active_admin(db: Session, user: User) -> None:
    count = db.scalar(
        select(func.count()).select_from(User)
        .join(user_role_assignments, user_role_assignments.c.user_id == User.id)
        .join(Role, Role.id == user_role_assignments.c.role_id)
        .where(Role.key == SUPER_ADMIN, User.is_active.is_(True), User.id != user.id)
    )
    if not count:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active super administrator must remain.")


def create_user(db: Session, email: str, password: str, role_key: str, full_name: str = "",
                client_id: uuid.UUID | None = None) -> User:
    email = email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists.")
    check_password(password)
    user = User(email=email, full_name=full_name, password_hash=hash_password(password))
    db.add(user)
    apply_role(db, user, role_key, client_id)
    db.flush()
    return user
