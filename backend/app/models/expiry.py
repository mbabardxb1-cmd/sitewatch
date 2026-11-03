"""Domain, hosting and SSL expiry records."""
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import Date, DateTime, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk, fk, str_enum


class DomainRecord(UUIDPk, Timestamps, Base):
    __tablename__ = "domain_records"
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), unique=True)
    domain: Mapped[str] = mapped_column(String(253))
    registrar: Mapped[str | None] = mapped_column(String(300))
    registrar_url: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nameservers: Mapped[list[str]] = mapped_column(ARRAY(String(253)), default=list)
    source: Mapped[str | None] = mapped_column(
        str_enum("rdap", "whois", "registrar_api", "manual", name="domain_source")
    )
    status: Mapped[str] = mapped_column(str_enum("ok", "unknown", "error", name="lookup_status"), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Last time an automated lookup (RDAP/WHOIS/registrar API) succeeded.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # True when a person set the domain name explicitly (instead of deriving it from the URL).
    domain_overridden: Mapped[bool] = mapped_column(default=False)
    # Manually entered fallback, kept separate so lookups never overwrite what a person typed.
    manual_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_registrar: Mapped[str | None] = mapped_column(String(300))
    manual_entered_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    manual_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HostingIntegration(UUIDPk, Timestamps, Base):
    """Credentials for a hosting provider API. Encrypted at rest; never serialized to clients."""

    __tablename__ = "hosting_integrations"
    provider_key: Mapped[str] = mapped_column(String(60))
    display_name: Mapped[str] = mapped_column(String(200))
    encrypted_credentials: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))


class HostingRecord(UUIDPk, Timestamps, Base):
    __tablename__ = "hosting_records"
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), unique=True)
    provider: Mapped[str | None] = mapped_column(String(200))
    plan: Mapped[str | None] = mapped_column(String(200))
    account_reference: Mapped[str | None] = mapped_column(String(200))
    renewal_date: Mapped[date | None] = mapped_column(Date)
    # 'manual' = entered by a person; 'api' = verified via provider API. NULL date => Unknown.
    source: Mapped[str | None] = mapped_column(str_enum("manual", "api", name="hosting_source"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entered_by: Mapped[uuid.UUID | None] = mapped_column(fk("users.id", ondelete="SET NULL"))
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    integration_id: Mapped[uuid.UUID | None] = mapped_column(fk("hosting_integrations.id", ondelete="SET NULL"))
    internal_notes: Mapped[str | None] = mapped_column(Text)


class SslCertificate(UUIDPk, Timestamps, Base):
    __tablename__ = "ssl_certificates"
    website_id: Mapped[uuid.UUID] = mapped_column(fk("websites.id"), index=True)
    hostname: Mapped[str] = mapped_column(String(253))
    issuer: Mapped[str | None] = mapped_column(String(500))
    subject: Mapped[str | None] = mapped_column(String(500))
    san: Mapped[list[str]] = mapped_column(ARRAY(String(253)), default=list)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid: Mapped[bool | None]
    validation_error: Mapped[str | None] = mapped_column(Text)
    # Set when no TLS handshake was possible at all (the check could not be performed).
    check_error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa.func.now(), index=True)
