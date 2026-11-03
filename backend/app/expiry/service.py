"""Domain, hosting and SSL expiry: refresh, effective dates and alerts.

Effective date rules (what the UI and reports show):
* Domain: the automated lookup's expiry if a lookup has ever succeeded (shown with its source,
  RDAP/WHOIS, and "last verified" time); otherwise the manually entered date, labelled Manual;
  otherwise Unknown.
* Hosting: API-verified renewal date if available, else manual, else Unknown.
* SSL: the most recent completed certificate check; a check that could not connect is shown as
  such and never replaces the last real observation.

Alerts: for each configured threshold (e.g. 60/30/14/7 days) one alert is sent per expiry date;
a renewal (new date) starts a fresh cycle. Only the tightest threshold crossed is sent, so a date
first seen at 10 days produces one "14 days" alert, not four.
"""
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.expiry import lookup
from app.expiry.hosting import REGISTRY, CredentialsUnavailable, HostingProviderError, decrypt_credentials
from app.models import Client, DomainRecord, HostingIntegration, HostingRecord, NotificationRule, SslCertificate, Website
from app.netsafe.http import BlockedTargetError, DnsResolutionError
from app.netsafe.tls import inspect_certificate
from app.notify.service import compose, queue_notification

log = logging.getLogger("sitewatch.expiry")


@dataclass
class Effective:
    value: date | None
    source: str  # rdap | whois | registrar_api | api | manual | unknown
    as_of: datetime | None  # when verified (automated) or entered (manual)
    note: str | None = None

    def days_left(self, today: date | None = None) -> int | None:
        return None if self.value is None else (self.value - (today or datetime.now(UTC).date())).days


# ---- Domain -----------------------------------------------------------------------------

def get_domain_record(db: Session, site: Website) -> DomainRecord:
    rec = db.scalar(select(DomainRecord).where(DomainRecord.website_id == site.id))
    if rec is None:
        rec = DomainRecord(website_id=site.id, domain=lookup.registrable_domain(site.hostname) or site.hostname,
                           status="unknown", nameservers=[])
        db.add(rec)
        db.flush()
    elif not rec.domain_overridden:
        rec.domain = lookup.registrable_domain(site.hostname) or site.hostname
    return rec


def domain_effective(rec: DomainRecord | None) -> Effective:
    if rec is not None and rec.verified_at is not None and rec.expires_at is not None:
        return Effective(rec.expires_at.date(), rec.source or "rdap", rec.verified_at,
                         None if rec.status == "ok" else f"Latest lookup failed: {rec.last_error}")
    if rec is not None and rec.manual_expires_at is not None:
        return Effective(rec.manual_expires_at.date(), "manual", rec.manual_entered_at,
                         "Entered manually; not verified with the registry")
    note = None
    if rec is not None and rec.last_error:
        note = rec.last_error
    elif rec is not None and rec.verified_at is not None:
        note = "The registry does not publish an expiry date for this domain"
    return Effective(None, "unknown", None, note)


def refresh_domain(db: Session, site: Website, now: datetime | None = None) -> DomainRecord:
    now = now or datetime.now(UTC)
    rec = get_domain_record(db, site)
    rec.last_checked_at = now
    if lookup.registrable_domain(rec.domain) is None and not rec.domain_overridden:
        rec.status, rec.last_error = "unknown", "Website address is an IP address or has no registrable domain"
        return rec
    try:
        info = lookup.lookup_domain(rec.domain)
    except lookup.DomainNotFound as e:
        rec.status, rec.last_error = "error", str(e)
        return rec
    except lookup.LookupError_ as e:
        rec.status, rec.last_error = "error", str(e)[:1000]
        return rec
    rec.source = info.source
    rec.expires_at = info.expires_at
    rec.registered_at = info.registered_at
    rec.registrar = info.registrar
    rec.registrar_url = info.registrar_url
    rec.nameservers = info.nameservers
    rec.verified_at = now
    if info.expires_at is None:
        rec.status, rec.last_error = "unknown", "The registry did not publish an expiry date"
    else:
        rec.status, rec.last_error = "ok", None
    return rec


def set_domain_manual(db: Session, site: Website, user_id, *, domain: str | None, expires_at: date | None,
                      registrar: str | None, clear: bool = False) -> DomainRecord:
    rec = get_domain_record(db, site)
    if domain is not None:
        domain = domain.strip().lower().rstrip(".")
        rec.domain_overridden = bool(domain)
        rec.domain = domain or (lookup.registrable_domain(site.hostname) or site.hostname)
        # A different domain invalidates earlier automated results.
        rec.verified_at = rec.expires_at = rec.registrar = None
        rec.status, rec.last_error = "unknown", None
    if clear:
        rec.manual_expires_at = rec.manual_registrar = rec.manual_entered_by = rec.manual_entered_at = None
    elif expires_at is not None or registrar is not None:
        if expires_at is not None:
            rec.manual_expires_at = datetime(expires_at.year, expires_at.month, expires_at.day, tzinfo=UTC)
        if registrar is not None:
            rec.manual_registrar = registrar.strip() or None
        rec.manual_entered_by, rec.manual_entered_at = user_id, datetime.now(UTC)
    return rec


# ---- Hosting ------------------------------------------------------------------------------

def get_hosting_record(db: Session, site: Website, create: bool = False) -> HostingRecord | None:
    rec = db.scalar(select(HostingRecord).where(HostingRecord.website_id == site.id))
    if rec is None and create:
        rec = HostingRecord(website_id=site.id)
        db.add(rec)
        db.flush()
    return rec


def hosting_effective(rec: HostingRecord | None) -> Effective:
    if rec is None or rec.renewal_date is None:
        return Effective(None, "unknown", None, "No renewal date recorded or verified")
    if rec.source == "api":
        return Effective(rec.renewal_date, "api", rec.verified_at, None)
    return Effective(rec.renewal_date, "manual", rec.entered_at, "Entered manually; not verified with the provider")


def refresh_hosting_api(db: Session, site: Website, now: datetime | None = None) -> HostingRecord:
    now = now or datetime.now(UTC)
    rec = get_hosting_record(db, site)
    if rec is None or rec.integration_id is None:
        raise HostingProviderError("No provider integration is linked to this website's hosting record.")
    integ = db.get(HostingIntegration, rec.integration_id)
    provider = REGISTRY.get(integ.provider_key) if integ else None
    if provider is None:
        raise HostingProviderError(f"Provider '{integ.provider_key if integ else '?'}' is not available in this installation.")
    try:
        result = provider.fetch(decrypt_credentials(integ.encrypted_credentials), rec.account_reference)
    except CredentialsUnavailable as e:
        raise HostingProviderError(str(e)) from None
    if result.renewal_date is None:
        raise HostingProviderError("The provider API did not return a renewal date.")
    rec.renewal_date, rec.source, rec.verified_at = result.renewal_date, "api", now
    if result.plan:
        rec.plan = result.plan
    if result.provider_name and not rec.provider:
        rec.provider = result.provider_name
    return rec


# ---- SSL --------------------------------------------------------------------------------------

def latest_ssl(db: Session, site_id, completed_only: bool = False) -> SslCertificate | None:
    q = select(SslCertificate).where(SslCertificate.website_id == site_id)
    if completed_only:
        q = q.where(SslCertificate.check_error.is_(None))
    return db.scalar(q.order_by(SslCertificate.checked_at.desc()).limit(1))


def refresh_ssl(db: Session, site: Website, now: datetime | None = None) -> SslCertificate:
    from urllib.parse import urlsplit

    now = now or datetime.now(UTC)
    port = urlsplit(site.base_url).port or 443
    row = SslCertificate(website_id=site.id, hostname=site.hostname, checked_at=now, san=[])
    try:
        info = inspect_certificate(site.hostname, port, timeout=float(site.timeout_s))
    except (BlockedTargetError, DnsResolutionError, OSError) as e:
        row.check_error = (str(e) or type(e).__name__)[:500]
    else:
        row.valid, row.validation_error = info.valid, info.error
        row.issuer, row.subject, row.san = info.issuer, info.subject, info.san
        row.not_before, row.not_after = info.not_before, info.not_after
    db.add(row)
    db.flush()
    return row


def ssl_effective(row: SslCertificate | None) -> Effective:
    if row is None or row.not_after is None:
        return Effective(None, "unknown", row.checked_at if row else None,
                         row.check_error if row is not None else "Not checked yet")
    return Effective(row.not_after.date(), "certificate", row.checked_at,
                     None if row.valid else f"Certificate is not valid: {row.validation_error}")


# ---- Scheduling ----------------------------------------------------------------------------

def _active_sites(db: Session) -> list[Website]:
    return list(db.scalars(select(Website).join(Client, Client.id == Website.client_id)
                           .where(Website.status == "active", Client.is_active.is_(True))).unique())


def refresh_due(db: Session, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    """Refresh what is due. Domains: daily within 60 days of expiry, else weekly; failed lookups
    retry after 6 h. SSL: daily (every 6 h within 14 days of expiry). Hosting APIs: daily."""
    now = now or datetime.now(UTC)
    counts = {"domains": 0, "ssl": 0, "hosting": 0}
    for site in _active_sites(db):
        rec = db.scalar(select(DomainRecord).where(DomainRecord.website_id == site.id))
        eff = domain_effective(rec)
        dl = eff.days_left(now.date())
        if rec is None or rec.last_checked_at is None:
            due = True
        elif rec.status == "error":
            due = now - rec.last_checked_at > timedelta(hours=6)
        else:
            due = now - rec.last_checked_at > (timedelta(days=1) if dl is not None and dl <= 60 else timedelta(days=7))
        if due and counts["domains"] < limit:
            refresh_domain(db, site, now)
            counts["domains"] += 1
            db.commit()

        if site.ssl_monitoring and site.base_url.startswith("https://") and counts["ssl"] < limit:
            last = latest_ssl(db, site.id)
            s_dl = ssl_effective(latest_ssl(db, site.id, completed_only=True)).days_left(now.date())
            interval = timedelta(hours=6) if s_dl is not None and s_dl <= 14 else timedelta(days=1)
            if last is None or now - last.checked_at > interval:
                refresh_ssl(db, site, now)
                counts["ssl"] += 1
                db.commit()

        h = get_hosting_record(db, site)
        if h is not None and h.integration_id and (h.verified_at is None or now - h.verified_at > timedelta(days=1)):
            try:
                refresh_hosting_api(db, site, now)
                counts["hosting"] += 1
            except HostingProviderError as e:
                log.warning("Hosting refresh for %s failed: %s", site.id, e)
            db.commit()
    return counts


# ---- Alerts --------------------------------------------------------------------------------

def thresholds_for(db: Session, site: Website, event: str, default: list[int]) -> list[int]:
    """Most specific enabled rule with thresholds wins: website > client > global."""
    rules = list(db.scalars(select(NotificationRule).where(
        NotificationRule.event_type == event, NotificationRule.enabled.is_(True),
        or_(NotificationRule.website_id == site.id,
            and_(NotificationRule.website_id.is_(None), NotificationRule.client_id == site.client_id),
            and_(NotificationRule.website_id.is_(None), NotificationRule.client_id.is_(None))))))
    rules.sort(key=lambda r: 0 if r.website_id else (1 if r.client_id else 2))
    for r in rules:
        days = (r.thresholds or {}).get("days_before")
        if isinstance(days, list) and days and all(isinstance(d, int) and 0 <= d <= 365 for d in days):
            return sorted(set(days), reverse=True)
    return sorted(set(default), reverse=True)


def _crossed(days_left: int, thresholds: list[int]) -> str | None:
    if days_left < 0:
        return "expired"
    hit = [t for t in thresholds if days_left <= t]
    return str(min(hit)) if hit else None


LABELS = {"domain_expiry": "Domain registration", "hosting_renewal": "Hosting", "ssl_expiry": "SSL certificate"}
SOURCE_TEXT = {"rdap": "verified via RDAP", "whois": "verified via WHOIS", "registrar_api": "verified via registrar API",
               "api": "verified via provider API", "manual": "entered manually (not verified)",
               "certificate": "read from the live certificate"}


def _alert(db: Session, site: Website, event: str, eff: Effective, today: date, defaults: list[int],
           extra: list[str] | None = None) -> int | None:
    if eff.value is None:
        return None
    dl = eff.days_left(today)
    level = _crossed(dl, thresholds_for(db, site, event, defaults))
    if level is None:
        return None
    what = LABELS[event]
    head = (f"{what} for {site.name} EXPIRED on {eff.value:%Y-%m-%d}." if level == "expired"
            else f"{what} for {site.name} expires in {dl} day{'s' if dl != 1 else ''} ({eff.value:%Y-%m-%d}).")
    _, body = compose(event, site, [head, f"Source: {SOURCE_TEXT.get(eff.source, eff.source)}"
                                    + (f", as of {eff.as_of:%Y-%m-%d}" if eff.as_of else "") + ".", *(extra or [])])
    subject = (f"[SiteWatch] {what} EXPIRED: {site.name}" if level == "expired"
               else f"[SiteWatch] {what} expires in {dl} day{'s' if dl != 1 else ''}: {site.name}")
    return queue_notification(db, event_type=event, website=site,
                              dedup_key=f"{event}:{site.id}:{eff.value.isoformat()}:{level}", subject=subject, body=body)


def send_expiry_alerts(db: Session, today: date | None = None) -> list[int]:
    s = get_settings()
    today = today or datetime.now(UTC).date()
    ids: list[int] = []
    for site in _active_sites(db):
        d = db.scalar(select(DomainRecord).where(DomainRecord.website_id == site.id))
        deff = domain_effective(d)
        extra = [f"Domain: {d.domain}", f"Registrar: {d.registrar or d.manual_registrar or 'unknown'}"] if d else []
        ids.append(_alert(db, site, "domain_expiry", deff, today, s.domain_alert_days, extra))

        h = get_hosting_record(db, site)
        heff = hosting_effective(h)
        ids.append(_alert(db, site, "hosting_renewal", heff, today, s.hosting_alert_days,
                          [f"Provider: {h.provider or 'unknown'}"] if h else []))

        if site.ssl_monitoring and site.base_url.startswith("https://"):
            row = latest_ssl(db, site.id, completed_only=True)
            seff = ssl_effective(row)
            if row is not None and row.valid is False and row.not_after and row.not_after.date() >= today:
                _, body = compose("ssl_expiry", site, [
                    f"The SSL certificate for {site.hostname} is NOT VALID: {row.validation_error}.",
                    "Visitors will see a browser security warning.",
                ])
                ids.append(queue_notification(db, event_type="ssl_expiry", website=site,
                                              dedup_key=f"ssl_invalid:{site.id}:{row.not_after.isoformat()}:{row.validation_error}"[:255],
                                              subject=f"[SiteWatch] SSL certificate is not valid: {site.name}", body=body))
            else:
                ids.append(_alert(db, site, "ssl_expiry", seff, today, s.ssl_alert_days,
                                  [f"Issuer: {row.issuer}"] if row else []))
    db.commit()
    return [i for i in ids if i]
