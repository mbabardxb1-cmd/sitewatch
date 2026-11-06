"""Client and website schemas.

There are deliberately separate *output* models for staff and client users. Client-facing models
simply do not contain technical/configuration fields, so they cannot leak by accident.
"""
import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


# ---- Clients ---------------------------------------------------------------------------

class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    contact_email: EmailStr | None = None
    show_expiry_to_client: bool = False

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError("Slug may contain lower-case letters, digits and hyphens only.")
        return v


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    show_expiry_to_client: bool | None = None
    is_active: bool | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    contact_email: str | None
    is_active: bool
    show_expiry_to_client: bool
    website_count: int = 0
    created_at: datetime


# ---- Crawl configuration ---------------------------------------------------------------

class CrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_depth: int = Field(default=3, ge=0, le=10)
    max_pages: int = Field(default=500, ge=1, le=10000)
    timeout_s: int = Field(default=15, ge=1, le=60)
    rate_rps: float = Field(default=2.0, ge=0.1, le=10.0, description="Max requests per second to the site")
    excluded_patterns: list[str] = Field(default_factory=list, max_length=100,
                                         description="Glob patterns (e.g. /wp-admin/*) or 're:' regex")
    check_external_links: bool = True
    render_js: bool = False
    slow_page_ms: int = Field(default=3000, ge=100, le=60000)

    @field_validator("excluded_patterns")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        out = []
        for p in v:
            p = p.strip()
            if not p:
                continue
            if len(p) > 500:
                raise ValueError("Pattern too long (max 500 characters).")
            if p.startswith("re:"):
                try:
                    re.compile(p[3:])
                except re.error as e:
                    raise ValueError(f"Invalid regular expression '{p}': {e}") from None
            out.append(p)
        return out


# ---- Websites --------------------------------------------------------------------------

def _check_codes(v: list[int] | None) -> list[int] | None:
    if v is None:
        return None
    if any(c < 100 or c > 599 for c in v):
        raise ValueError("Status codes must be between 100 and 599.")
    return sorted(set(v))


class MonitoringSettings(BaseModel):
    check_interval_s: int = Field(default=300, ge=60, le=86400, description="1 minute to 24 hours")
    retry_attempts: int = Field(default=2, ge=0, le=10)
    recovery_threshold: int = Field(default=1, ge=1, le=10)
    timeout_s: int = Field(default=15, ge=2, le=60)
    degraded_threshold_ms: int = Field(default=3000, ge=100, le=60000)
    expected_status_codes: list[int] = Field(default_factory=lambda: [200], min_length=1, max_length=20)
    audit_day_of_month: int = Field(default=1, ge=1, le=28)
    auto_audit: bool = True
    ssl_monitoring: bool = True
    notification_recipients: list[EmailStr] = Field(default_factory=list, max_length=50)

    _codes = field_validator("expected_status_codes")(_check_codes)


class WebsiteCreate(MonitoringSettings):
    client_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    crawl_config: CrawlConfig = Field(default_factory=CrawlConfig)


class WebsiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    status: Literal["active", "paused"] | None = None
    check_interval_s: int | None = Field(default=None, ge=60, le=86400)
    retry_attempts: int | None = Field(default=None, ge=0, le=10)
    recovery_threshold: int | None = Field(default=None, ge=1, le=10)
    timeout_s: int | None = Field(default=None, ge=2, le=60)
    degraded_threshold_ms: int | None = Field(default=None, ge=100, le=60000)
    expected_status_codes: list[int] | None = Field(default=None, min_length=1, max_length=20)
    audit_day_of_month: int | None = Field(default=None, ge=1, le=28)
    auto_audit: bool | None = None
    ssl_monitoring: bool | None = None
    notification_recipients: list[EmailStr] | None = Field(default=None, max_length=50)

    _codes = field_validator("expected_status_codes")(_check_codes)


class AssignmentsUpdate(BaseModel):
    user_ids: list[uuid.UUID] = Field(max_length=200)


class AssignedUser(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str


class WebsiteClientView(BaseModel):
    """What a client (read-only) user may see about a website."""
    id: uuid.UUID
    name: str
    base_url: str
    client_id: uuid.UUID
    client_name: str
    status: str
    current_state: str
    health: Literal["healthy", "warning", "critical", "unknown"]
    health_reason: str
    last_checked_at: datetime | None
    last_audit_at: datetime | None


class WebsiteStaffView(WebsiteClientView):
    hostname: str
    check_interval_s: int
    retry_attempts: int
    recovery_threshold: int
    timeout_s: int
    degraded_threshold_ms: int
    expected_status_codes: list[int]
    next_check_at: datetime | None
    audit_day_of_month: int
    auto_audit: bool
    crawl_config: dict
    ssl_monitoring: bool
    notification_recipients: list[str]
    assigned_users: list[AssignedUser]
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
