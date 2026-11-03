"""Domain, hosting and SSL expiry API."""
import re
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.auth.ratelimit import RateLimiter, get_redis
from app.auth.scope import get_website_or_404, website_scope_clause
from app.db import get_db
from app.expiry import service as svc
from app.expiry.hosting import REGISTRY, CredentialsUnavailable, HostingProviderError, encrypt_credentials
from app.expiry.lookup import registrable_domain
from app.models import Client, DomainRecord, HostingIntegration, SslCertificate, User, Website
from app.services.audit_log import record
from app.services.website_service import is_client_user

router = APIRouter(tags=["expiry"])
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


class EffectiveOut(BaseModel):
    date: date | None
    days_left: int | None
    source: str
    as_of: datetime | None
    note: str | None
    level: str  # healthy | warning | critical | unknown


def _level(kind: str, eff: svc.Effective, invalid: bool = False) -> str:
    if invalid:
        return "critical"
    dl = eff.days_left()
    if dl is None:
        return "unknown"
    crit, warn = (7, 30) if kind == "ssl" else (14, 60)
    return "critical" if dl <= crit else ("warning" if dl <= warn else "healthy")


def _eff(kind: str, eff: svc.Effective, invalid: bool = False) -> EffectiveOut:
    return EffectiveOut(date=eff.value, days_left=eff.days_left(), source=eff.source, as_of=eff.as_of,
                        note=eff.note, level=_level(kind, eff, invalid))


def _email(db: Session, uid) -> str | None:
    u = db.get(User, uid) if uid else None
    return u.email if u else None


def _check_access(db: Session, user: User, site: Website) -> None:
    if is_client_user(user):
        client = db.get(Client, site.client_id)
        if not client or not client.show_expiry_to_client:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Expiry information has not been enabled for your organization.")


def domain_view(db: Session, site: Website, user: User) -> dict:
    rec = db.scalar(select(DomainRecord).where(DomainRecord.website_id == site.id))
    eff = svc.domain_effective(rec)
    domain = rec.domain if rec else registrable_domain(site.hostname)
    if rec is not None and not rec.domain_overridden and registrable_domain(rec.domain) is None:
        domain = None  # e.g. an IP address: there is no registered domain to report
    registrar = (rec.registrar or rec.manual_registrar) if rec else None
    out = {"domain": domain, "registrar": registrar, "effective": _eff("domain", eff)}
    if is_client_user(user):
        return out
    out.update({
        "domain_overridden": bool(rec and rec.domain_overridden),
        "registrar_url": rec.registrar_url if rec else None,
        "nameservers": rec.nameservers if rec else [],
        "registered_at": rec.registered_at if rec else None,
        "lookup": None if rec is None else {
            "status": rec.status, "source": rec.source, "expires_at": rec.expires_at, "verified_at": rec.verified_at,
            "last_checked_at": rec.last_checked_at, "last_error": rec.last_error},
        "manual": None if rec is None or rec.manual_entered_at is None else {
            "expires_at": rec.manual_expires_at, "registrar": rec.manual_registrar,
            "entered_by": _email(db, rec.manual_entered_by), "entered_at": rec.manual_entered_at},
    })
    return out


def hosting_view(db: Session, site: Website, user: User) -> dict:
    rec = svc.get_hosting_record(db, site)
    eff = svc.hosting_effective(rec)
    out = {"provider": rec.provider if rec else None, "effective": _eff("hosting", eff)}
    if is_client_user(user):
        return out
    integ = db.get(HostingIntegration, rec.integration_id) if rec and rec.integration_id else None
    out.update({
        "plan": rec.plan if rec else None,
        "account_reference": rec.account_reference if rec else None,
        "renewal_date": rec.renewal_date if rec else None,
        "source": rec.source if rec else None,
        "verified_at": rec.verified_at if rec else None,
        "entered_by": _email(db, rec.entered_by) if rec else None,
        "entered_at": rec.entered_at if rec else None,
        "integration": {"id": str(integ.id), "name": integ.display_name, "provider_key": integ.provider_key} if integ else None,
        "internal_notes": rec.internal_notes if rec else None,
    })
    return out


def ssl_view(db: Session, site: Website, user: User) -> dict:
    if not site.base_url.startswith("https://"):
        return {"applicable": False, "reason": "Website uses http://", "effective": _eff("ssl", svc.Effective(None, "unknown", None, "Not applicable"))}
    if not site.ssl_monitoring:
        return {"applicable": False, "reason": "SSL monitoring is turned off for this website",
                "effective": _eff("ssl", svc.Effective(None, "unknown", None, "SSL monitoring disabled"))}
    row = svc.latest_ssl(db, site.id, completed_only=True)
    attempt = svc.latest_ssl(db, site.id)
    eff = svc.ssl_effective(row)
    invalid = bool(row and row.valid is False)
    out = {"applicable": True, "effective": _eff("ssl", eff, invalid), "valid": row.valid if row else None,
           "issuer": row.issuer if row else None}
    if is_client_user(user):
        return out
    history = db.scalars(select(SslCertificate).where(SslCertificate.website_id == site.id)
                         .order_by(SslCertificate.checked_at.desc()).limit(10))
    out.update({
        "subject": row.subject if row else None, "san": row.san if row else [],
        "not_before": row.not_before if row else None, "not_after": row.not_after if row else None,
        "validation_error": row.validation_error if row else None,
        "last_attempt": None if attempt is None else {"checked_at": attempt.checked_at, "check_error": attempt.check_error},
        "history": [{"checked_at": h.checked_at, "valid": h.valid, "not_after": h.not_after, "issuer": h.issuer,
                     "check_error": h.check_error} for h in history],
    })
    return out


@router.get("/expiry/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(require("expiry.read"))):
    q = (select(Website).join(Client, Client.id == Website.client_id)
         .where(website_scope_clause(user), Website.status != "archived").order_by(Website.name))
    if is_client_user(user):
        q = q.where(Client.show_expiry_to_client.is_(True))
    rows = []
    for site in db.scalars(q).unique():
        d, h, s = domain_view(db, site, user), hosting_view(db, site, user), ssl_view(db, site, user)
        rows.append({"website_id": str(site.id), "website_name": site.name, "base_url": site.base_url,
                     "client_name": site.client.name,
                     "domain": {"domain": d["domain"], "registrar": d["registrar"], **d["effective"].model_dump(mode="json"),
                                **({"lookup_status": (d.get("lookup") or {}).get("status")} if not is_client_user(user) else {})},
                     "hosting": {"provider": h["provider"], **h["effective"].model_dump(mode="json")},
                     "ssl": {"applicable": s["applicable"], "issuer": s.get("issuer"), "valid": s.get("valid"),
                             **s["effective"].model_dump(mode="json")}})
    return {"enabled": True, "websites": rows}


@router.get("/websites/{website_id}/expiry")
def website_expiry(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("expiry.read"))):
    site = get_website_or_404(db, user, website_id)
    _check_access(db, user, site)
    return {"domain": domain_view(db, site, user), "hosting": hosting_view(db, site, user), "ssl": ssl_view(db, site, user)}


def _throttle(key: str, seconds: int = 60) -> None:
    if not RateLimiter(get_redis()).hit(key, 1, seconds):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Refreshed recently; try again in {seconds}s.")


class DomainUpdate(BaseModel):
    domain: str | None = Field(default=None, max_length=253, description="Override the domain to look up; '' resets")
    manual_expires_at: date | None = None
    manual_registrar: str | None = Field(default=None, max_length=300)
    clear_manual: bool = False


@router.put("/websites/{website_id}/domain")
def update_domain(website_id: uuid.UUID, body: DomainUpdate, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("expiry.write"))):
    site = get_website_or_404(db, user, website_id)
    if body.domain:
        d = body.domain.strip().lower().rstrip(".")
        try:
            d = d.encode("idna").decode("ascii")
        except UnicodeError:
            raise HTTPException(422, "Invalid domain name.") from None
        if not HOST_RE.match(d) or registrable_domain(d) != d:
            raise HTTPException(422, "Enter a registrable domain such as example.com or example.co.uk.")
        body.domain = d
    svc.set_domain_manual(db, site, user.id, domain=body.domain, expires_at=body.manual_expires_at,
                          registrar=body.manual_registrar, clear=body.clear_manual)
    record(db, user.id, "expiry.domain_update", "website", site.id, client_ip(request),
           **body.model_dump(mode="json", exclude_none=True))
    db.commit()
    return domain_view(db, site, user)


@router.post("/websites/{website_id}/domain/refresh")
def refresh_domain(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("expiry.write"))):
    site = get_website_or_404(db, user, website_id)
    _throttle(f"domainrefresh:{site.id}")
    svc.refresh_domain(db, site)
    db.commit()
    return domain_view(db, site, user)


class HostingUpdate(BaseModel):
    provider: str | None = Field(default=None, max_length=200)
    plan: str | None = Field(default=None, max_length=200)
    account_reference: str | None = Field(default=None, max_length=200)
    renewal_date: date | None = None
    clear_renewal_date: bool = False
    internal_notes: str | None = Field(default=None, max_length=5000)
    integration_id: uuid.UUID | None = None
    clear_integration: bool = False


@router.put("/websites/{website_id}/hosting")
def update_hosting(website_id: uuid.UUID, body: HostingUpdate, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("expiry.write"))):
    site = get_website_or_404(db, user, website_id)
    rec = svc.get_hosting_record(db, site, create=True)
    for k in ("provider", "plan", "account_reference", "internal_notes"):
        v = getattr(body, k)
        if v is not None:
            setattr(rec, k, v.strip() or None)
    if body.clear_renewal_date:
        rec.renewal_date, rec.source, rec.verified_at = None, None, None
    elif body.renewal_date is not None:
        # A person typed this date: it is manual, whatever was there before.
        rec.renewal_date, rec.source, rec.verified_at = body.renewal_date, "manual", None
        rec.entered_by, rec.entered_at = user.id, datetime.now(UTC)
    if body.clear_integration:
        rec.integration_id = None
    elif body.integration_id is not None:
        if db.get(HostingIntegration, body.integration_id) is None:
            raise HTTPException(422, "Integration not found.")
        rec.integration_id = body.integration_id
    record(db, user.id, "expiry.hosting_update", "website", site.id, client_ip(request),
           fields=sorted(body.model_dump(exclude_none=True, exclude={"internal_notes"}).keys()))
    db.commit()
    return hosting_view(db, site, user)


@router.post("/websites/{website_id}/hosting/refresh")
def refresh_hosting(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("expiry.write"))):
    site = get_website_or_404(db, user, website_id)
    _throttle(f"hostingrefresh:{site.id}")
    try:
        svc.refresh_hosting_api(db, site)
    except HostingProviderError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    db.commit()
    return hosting_view(db, site, user)


@router.post("/websites/{website_id}/ssl/refresh")
def refresh_ssl(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("expiry.write"))):
    site = get_website_or_404(db, user, website_id)
    if not site.base_url.startswith("https://"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This website uses http://; there is no certificate to check.")
    _throttle(f"sslrefresh:{site.id}")
    svc.refresh_ssl(db, site)
    db.commit()
    return ssl_view(db, site, user)


# ---- Hosting provider integrations (administrators) ---------------------------------------

class IntegrationIn(BaseModel):
    provider_key: str = Field(max_length=60)
    display_name: str = Field(min_length=1, max_length=200)
    credentials: dict[str, str]


@router.get("/integrations/hosting/providers")
def providers(_: User = Depends(require("integration.manage"))):
    return [{"key": p.key, "display_name": p.display_name, "credential_fields": p.credential_fields}
            for p in REGISTRY.values()]


@router.get("/integrations/hosting")
def list_integrations(db: Session = Depends(get_db), _: User = Depends(require("integration.manage"))):
    # Credentials are never returned, not even to administrators.
    return [{"id": str(i.id), "provider_key": i.provider_key, "display_name": i.display_name,
             "provider_available": i.provider_key in REGISTRY, "created_at": i.created_at}
            for i in db.scalars(select(HostingIntegration).order_by(HostingIntegration.display_name))]


@router.post("/integrations/hosting", status_code=201)
def create_integration(body: IntegrationIn, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("integration.manage"))):
    provider = REGISTRY.get(body.provider_key)
    if provider is None:
        raise HTTPException(422, "Unknown hosting provider. No provider API integrations are installed; "
                                 "use manual renewal dates instead.")
    missing = [f for f in provider.credential_fields if not body.credentials.get(f)]
    if missing:
        raise HTTPException(422, f"Missing credential fields: {', '.join(missing)}")
    try:
        blob = encrypt_credentials({k: body.credentials[k] for k in provider.credential_fields})
    except CredentialsUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
    integ = HostingIntegration(provider_key=provider.key, display_name=body.display_name,
                               encrypted_credentials=blob, created_by=user.id)
    db.add(integ)
    db.flush()
    record(db, user.id, "integration.create", "hosting_integration", integ.id, client_ip(request),
           provider=provider.key)
    db.commit()
    return {"id": str(integ.id), "provider_key": integ.provider_key, "display_name": integ.display_name}


@router.delete("/integrations/hosting/{integration_id}", status_code=204)
def delete_integration(integration_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("integration.manage"))):
    integ = db.get(HostingIntegration, integration_id)
    if integ is None:
        raise HTTPException(404, "Not found")
    db.delete(integ)
    record(db, user.id, "integration.delete", "hosting_integration", integration_id, client_ip(request))
    db.commit()
