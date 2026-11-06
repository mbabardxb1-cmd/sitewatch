import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk, fk, str_enum

WEBSITE_STATUS = ("active", "paused", "archived")
WEBSITE_STATE = ("up", "down", "degraded", "unknown")

DEFAULT_CRAWL_CONFIG = {
    "max_depth": 3,
    "max_pages": 500,
    "timeout_s": 15,
    "rate_rps": 2.0,
    "excluded_patterns": [],
    "check_external_links": True,
    "render_js": False,
    "slow_page_ms": 3000,
}


class Client(UUIDPk, Timestamps, Base):
    __tablename__ = "clients"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(default=True)
    # Whether client users may see domain/hosting/SSL expiry info.
    show_expiry_to_client: Mapped[bool] = mapped_column(default=False)

    websites: Mapped[list["Website"]] = relationship(back_populates="client")


website_user_assignments = sa.Table(
    "website_user_assignments",
    Base.metadata,
    sa.Column("website_id", sa.Uuid, fk("websites.id"), primary_key=True),
    sa.Column("user_id", sa.Uuid, fk("users.id"), primary_key=True, index=True),
    sa.Column("created_at", DateTime(timezone=True), server_default=sa.func.now()),
)


class Website(UUIDPk, Timestamps, Base):
    __tablename__ = "websites"
    __table_args__ = (
        sa.Index("ix_websites_due", "status", "next_check_at"),
        sa.CheckConstraint("check_interval_s BETWEEN 60 AND 86400", name="check_interval_range"),
        sa.CheckConstraint("audit_day_of_month BETWEEN 1 AND 28", name="audit_day_range"),
        sa.CheckConstraint("retry_attempts BETWEEN 0 AND 10", name="retry_range"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(fk("clients.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(2048))
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(str_enum(*WEBSITE_STATUS, name="website_status"), default="active")

    # Uptime configuration
    check_interval_s: Mapped[int] = mapped_column(default=300)
    retry_attempts: Mapped[int] = mapped_column(default=2)
    recovery_threshold: Mapped[int] = mapped_column(default=1)
    timeout_s: Mapped[int] = mapped_column(default=15)
    degraded_threshold_ms: Mapped[int] = mapped_column(default=3000)
    expected_status_codes: Mapped[list[int]] = mapped_column(ARRAY(sa.Integer), default=lambda: [200])
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_state: Mapped[str] = mapped_column(str_enum(*WEBSITE_STATE, name="website_state"), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    consecutive_successes: Mapped[int] = mapped_column(default=0)
    # Start of the current run of failed checks (= incident start once the outage is confirmed).
    failure_streak_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    degraded_streak: Mapped[int] = mapped_column(default=0)
    degraded_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Audit configuration
    audit_day_of_month: Mapped[int] = mapped_column(default=1)
    # Run the scheduled monthly audit automatically (manual audits are always possible).
    auto_audit: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    crawl_config: Mapped[dict] = mapped_column(JSONB, default=lambda: dict(DEFAULT_CRAWL_CONFIG))
    last_audit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ssl_monitoring: Mapped[bool] = mapped_column(default=True)
    notification_recipients: Mapped[list[str]] = mapped_column(ARRAY(String(320)), default=list)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped[Client] = relationship(back_populates="websites", lazy="joined")
    assigned_users: Mapped[list["User"]] = relationship(  # noqa: F821
        secondary=website_user_assignments, lazy="selectin"
    )
