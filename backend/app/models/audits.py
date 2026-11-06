"""Technical audit runs, findings, tracked issues and comments."""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk, fk, str_enum

SEVERITIES = ("critical", "high", "medium", "low", "info")
CERTAINTY = ("confirmed", "warning", "could_not_check")
AUDIT_STATUS = ("queued", "running", "completed", "partial", "failed", "cancelled")
ISSUE_STATUS = ("open", "acknowledged", "in_progress", "resolved_pending_verification", "resolved")


class AuditRun(UUIDPk, Timestamps, Base):
    __tablename__ = "audit_runs"
    __table_args__ = (
        # Scheduled audits are unique per website and month -> no duplicate scheduled runs.
        sa.Index(
            "uq_audit_runs_scheduled_period",
            "website_id",
            "period",
            unique=True,
            postgresql_where=sa.text("trigger = 'scheduled'"),
        ),
        sa.Index("ix_audit_runs_site_created", "website_id", sa.text("created_at DESC")),
    )
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"))
    period: Mapped[str | None] = mapped_column(String(7))  # YYYY-MM
    trigger: Mapped[str] = mapped_column(str_enum("scheduled", "manual", name="audit_trigger"))
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(str_enum(*AUDIT_STATUS, name="audit_status"), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_crawled: Mapped[int] = mapped_column(default=0)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    # {check_key: {"status": "performed" | "not_checked" | "error", "reason": str}}
    check_coverage: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Live progress while running: {"percent", "phase", "pages_crawled", "updated_at", "expected_s"}
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=sa.text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)


class MonitoredUrl(UUIDPk, Timestamps, Base):
    __tablename__ = "monitored_urls"
    __table_args__ = (sa.UniqueConstraint("website_id", "url_hash", name="uq_monitored_urls_site_hash"),)
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    url_hash: Mapped[str] = mapped_column(String(64))
    is_important: Mapped[bool] = mapped_column(default=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[int | None]


class AuditFinding(UUIDPk, Timestamps, Base):
    __tablename__ = "audit_findings"
    __table_args__ = (sa.Index("ix_audit_findings_site_fp", "website_id", "fingerprint"),)
    audit_run_id: Mapped[uuid.UUID] = mapped_column(fk("audit_runs.id"), index=True)
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"))
    check_key: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(str_enum(*SEVERITIES, name="severity"))
    certainty: Mapped[str] = mapped_column(str_enum(*CERTAINTY, name="finding_certainty"))
    title: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str | None] = mapped_column(String(2048))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))


class Issue(UUIDPk, Timestamps, Base):
    """A finding tracked across audits (new / recurring / resolved)."""

    __tablename__ = "issues"
    __table_args__ = (sa.UniqueConstraint("website_id", "fingerprint", name="uq_issues_site_fp"),)
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    check_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(str_enum(*SEVERITIES, name="severity"))
    status: Mapped[str] = mapped_column(str_enum(*ISSUE_STATUS, name="issue_status"), default="open")
    certainty: Mapped[str] = mapped_column(str_enum(*CERTAINTY, name="finding_certainty"), default="confirmed")
    url: Mapped[str | None] = mapped_column(String(2048))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    # How resolution was verified (set only by an audit that no longer observed the problem).
    resolution_note: Mapped[str | None] = mapped_column(Text)
    reopened_count: Mapped[int] = mapped_column(default=0)
    first_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("audit_runs.id", ondelete="SET NULL"))
    last_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("audit_runs.id", ondelete="SET NULL"))
    resolved_verified_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("audit_runs.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(default=1)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))


class IssueComment(UUIDPk, Timestamps, Base):
    """Internal developer notes. Never exposed to client users."""

    __tablename__ = "issue_comments"
    issue_id: Mapped[uuid.UUID] = mapped_column(fk("issues.id"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(str_enum("internal", name="comment_visibility"), default="internal")
