"""Notifications, monthly reports and system settings."""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk, fk, str_enum

NOTIFICATION_EVENTS = (
    "website_down",
    "website_recovered",
    "repeated_server_errors",
    "response_degraded",
    "critical_audit_findings",
    "domain_expiry",
    "hosting_renewal",
    "ssl_expiry",
    "audit_completed",
    "report_available",
)


class NotificationRule(UUIDPk, Timestamps, Base):
    __tablename__ = "notification_rules"
    website_id: Mapped[uuid.UUID | None] = mapped_column(fk("websites.id"), index=True)  # NULL = global
    client_id: Mapped[uuid.UUID | None] = mapped_column(fk("clients.id"), index=True)
    event_type: Mapped[str] = mapped_column(str_enum(*NOTIFICATION_EVENTS, name="notification_event"))
    enabled: Mapped[bool] = mapped_column(default=True)
    channel: Mapped[str] = mapped_column(str_enum("email", name="notification_channel"), default="email")
    recipients: Mapped[list[str]] = mapped_column(ARRAY(String(320)), default=list)
    thresholds: Mapped[dict] = mapped_column(JSONB, default=dict)
    min_interval_s: Mapped[int] = mapped_column(default=3600)


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    __table_args__ = (sa.UniqueConstraint("dedup_key", "channel", name="uq_notification_logs_dedup"),)
    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(fk("notification_rules.id", ondelete="SET NULL"))
    website_id: Mapped[uuid.UUID | None] = mapped_column(fk("websites.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    dedup_key: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(20))
    recipients: Mapped[list[str]] = mapped_column(ARRAY(String(320)), default=list)
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(str_enum("pending", "sent", "failed", "suppressed", name="notification_status"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa.func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MonthlyReport(UUIDPk, Timestamps, Base):
    __tablename__ = "monthly_reports"
    __table_args__ = (sa.UniqueConstraint("website_id", "period", "kind", name="uq_monthly_reports_period"),)
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    kind: Mapped[str] = mapped_column(str_enum("client", "technical", name="report_kind"))
    status: Mapped[str] = mapped_column(
        str_enum("generating", "ready", "failed", name="report_status"), default="generating"
    )
    approved_for_client: Mapped[bool] = mapped_column(default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None]
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )
