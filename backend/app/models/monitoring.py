"""Uptime checks, incidents and performance rollups."""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk, fk, str_enum

CHECK_ERROR_TYPES = ("timeout", "dns", "connect", "ssl", "http_status", "too_many_redirects", "blocked", "other")
INCIDENT_STATUS = ("open", "acknowledged", "resolved")


class UptimeCheck(Base):
    __tablename__ = "uptime_checks"
    __table_args__ = (sa.Index("ix_uptime_checks_site_time", "website_id", sa.text("checked_at DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa.func.now())
    ok: Mapped[bool]
    degraded: Mapped[bool] = mapped_column(default=False)
    http_status: Mapped[int | None]
    response_ms: Mapped[int | None]
    error_type: Mapped[str | None] = mapped_column(str_enum(*CHECK_ERROR_TYPES, name="check_error_type"))
    error_detail: Mapped[str | None] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(String(2048))
    attempt: Mapped[int] = mapped_column(default=1)
    is_retry: Mapped[bool] = mapped_column(default=False)
    is_manual: Mapped[bool] = mapped_column(default=False)


class UptimeIncident(UUIDPk, Timestamps, Base):
    __tablename__ = "uptime_incidents"
    __table_args__ = (
        # At most one unresolved incident per website.
        sa.Index(
            "uq_uptime_incidents_one_open",
            "website_id",
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
        ),
    )
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None]
    status: Mapped[str] = mapped_column(str_enum(*INCIDENT_STATUS, name="incident_status"), default="open")
    cause_summary: Mapped[str] = mapped_column(String(500), default="")
    first_check_id: Mapped[int | None] = mapped_column(BigInteger)
    last_check_id: Mapped[int | None] = mapped_column(BigInteger)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PerformanceMetric(Base):
    """Hourly rollup of uptime checks for fast dashboards and long-term retention."""

    __tablename__ = "performance_metrics"
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    checks: Mapped[int] = mapped_column(default=0)
    successes: Mapped[int] = mapped_column(default=0)
    avg_ms: Mapped[int | None]
    p95_ms: Mapped[int | None]
    max_ms: Mapped[int | None]
