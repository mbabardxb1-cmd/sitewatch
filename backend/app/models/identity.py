import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk, fk


class Role(UUIDPk, Timestamps, Base):
    __tablename__ = "roles"
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    permissions: Mapped[list["Permission"]] = relationship(
        secondary="role_permissions", lazy="selectin"
    )


class Permission(UUIDPk, Timestamps, Base):
    __tablename__ = "permissions"
    key: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


role_permissions = sa.Table(
    "role_permissions",
    Base.metadata,
    sa.Column("role_id", sa.Uuid, fk("roles.id"), primary_key=True),
    sa.Column("permission_id", sa.Uuid, fk("permissions.id"), primary_key=True),
)

user_role_assignments = sa.Table(
    "user_role_assignments",
    Base.metadata,
    sa.Column("user_id", sa.Uuid, fk("users.id"), primary_key=True),
    sa.Column("role_id", sa.Uuid, fk("roles.id"), primary_key=True),
    sa.Column("created_at", DateTime(timezone=True), server_default=sa.func.now()),
)


user_permission_overrides = sa.Table(
    "user_permission_overrides",
    Base.metadata,
    sa.Column("user_id", sa.Uuid, fk("users.id"), primary_key=True),
    sa.Column("permission_id", sa.Uuid, fk("permissions.id"), primary_key=True),
    # True = granted on top of the role, False = removed from the role's permissions.
    sa.Column("granted", sa.Boolean, nullable=False),
    sa.Column("created_at", DateTime(timezone=True), server_default=sa.func.now()),
)


class PermissionOverride(Base):
    __table__ = user_permission_overrides
    permission: Mapped["Permission"] = relationship(lazy="joined")


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    # Set only for client (read-only) users: the organization they belong to.
    client_id: Mapped[uuid.UUID | None] = mapped_column(fk("clients.id", ondelete="RESTRICT"), index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[Role]] = relationship(secondary=user_role_assignments, lazy="selectin")
    permission_overrides: Mapped[list[PermissionOverride]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", primaryjoin="User.id == PermissionOverride.user_id")

    @property
    def role_keys(self) -> set[str]:
        return {r.key for r in self.roles}

    @property
    def role_permission_keys(self) -> set[str]:
        return {p.key for r in self.roles for p in r.permissions}

    @property
    def permission_keys(self) -> set[str]:
        """Role permissions, adjusted by per-user overrides (staff only; see user_service)."""
        keys = self.role_permission_keys
        for o in self.permission_overrides:
            if o.granted:
                keys.add(o.permission.key)
            else:
                keys.discard(o.permission.key)
        return keys


class UserSession(UUIDPk, Timestamps, Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID] = mapped_column(fk("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))

    user: Mapped[User] = relationship(lazy="joined")


class AuditLog(UUIDPk, Base):
    """Security trail of privileged actions (not to be confused with website audits)."""

    __tablename__ = "audit_log"
    actor_id: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa.func.now(), index=True)
