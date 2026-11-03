"""Domain registration lookups: RDAP (RFC 7480-7484, 9083) with a WHOIS (RFC 3912) fallback.

Only facts actually returned by the registry are recorded. Missing fields stay empty and the
caller reports them as unknown; nothing is estimated.
"""
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

import tldextract

from app.auth.ratelimit import get_redis
from app.config import get_settings
from app.netsafe.http import BlockedTargetError, DnsResolutionError, TooManyRedirects, UnsafeUrlError, safe_client, safe_fetch
from app.netsafe.tls import _connect

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # bundled list, no runtime downloads
BOOTSTRAP_CACHE_KEY = "sitewatch:rdap:bootstrap"
BOOTSTRAP_TTL_S = 24 * 3600
MAX_WHOIS_BYTES = 256 * 1024


class LookupError_(Exception):
    """Lookup failed (network, malformed response). Message is safe to show to staff."""


class LookupUnavailable(LookupError_):
    """This method does not support the TLD (e.g. no RDAP server published)."""


class DomainNotFound(LookupError_):
    pass


@dataclass
class DomainInfo:
    domain: str
    source: str  # rdap | whois
    expires_at: datetime | None = None
    registered_at: datetime | None = None
    registrar: str | None = None
    registrar_url: str | None = None
    nameservers: list[str] = field(default_factory=list)
    reference: str | None = None  # RDAP URL or WHOIS server used


def registrable_domain(hostname: str) -> str | None:
    """'shop.example.co.uk' -> 'example.co.uk'. None for IP addresses / unknown suffixes."""
    host = hostname.strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    r = _extract(host)
    return r.top_domain_under_public_suffix or None


def _parse_date(value: str) -> datetime | None:
    value = value.strip().split(" (")[0].strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%Y.%m.%d", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%Y",
                "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%a %b %d %H:%M:%S %Z %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


# ---- RDAP ---------------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 15) -> tuple[int, dict | None]:
    s = get_settings()
    if not url.startswith("https://") and (s.is_production or not s.private_target_allowlist):
        raise LookupError_(f"Refusing non-HTTPS RDAP URL {url}")
    try:
        with safe_client(timeout) as c:
            c.headers["Accept"] = "application/rdap+json, application/json"
            r = safe_fetch(c, url, read_body_bytes=2 * 1024 * 1024)
    except (BlockedTargetError, UnsafeUrlError, DnsResolutionError, TooManyRedirects) as e:
        raise LookupError_(str(e)) from None
    except Exception as e:  # noqa: BLE001 - httpx errors
        raise LookupError_(f"{type(e).__name__}: {e}"[:300]) from None
    if r.status >= 400:
        return r.status, None
    try:
        return r.status, json.loads(r.body.decode("utf-8", "replace"))
    except ValueError:
        raise LookupError_(f"Invalid JSON from {url}") from None


def rdap_bootstrap() -> dict[str, list[str]]:
    redis = get_redis()
    try:
        cached = redis.get(BOOTSTRAP_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001
        pass
    status, data = _get_json(get_settings().rdap_bootstrap_url)
    if not data or "services" not in data:
        raise LookupError_(f"RDAP bootstrap unavailable (HTTP {status})")
    mapping: dict[str, list[str]] = {}
    for tlds, urls in data["services"]:
        https = [u for u in urls if u.startswith("https://")] or urls
        for t in tlds:
            mapping[t.lower()] = https
    try:
        redis.set(BOOTSTRAP_CACHE_KEY, json.dumps(mapping), ex=BOOTSTRAP_TTL_S)
    except Exception:  # noqa: BLE001
        pass
    return mapping


def _vcard_name(entity: dict) -> str | None:
    vcard = entity.get("vcardArray")
    if isinstance(vcard, list) and len(vcard) == 2:
        for item in vcard[1]:
            if isinstance(item, list) and len(item) >= 4 and item[0] in ("fn", "org") and item[3]:
                return str(item[3])
    return None


def parse_rdap(domain: str, data: dict, url: str) -> DomainInfo:
    info = DomainInfo(domain=domain, source="rdap", reference=url)
    for ev in data.get("events", []) or []:
        action, date = (ev.get("eventAction") or "").lower(), ev.get("eventDate")
        if not date:
            continue
        if action == "expiration":
            info.expires_at = _parse_date(date)
        elif action == "registration":
            info.registered_at = _parse_date(date)
    for ent in data.get("entities", []) or []:
        if "registrar" in [r.lower() for r in ent.get("roles", [])]:
            info.registrar = _vcard_name(ent) or ent.get("handle")
            for link in ent.get("links", []) or []:
                if link.get("rel") in ("about", "self") and str(link.get("href", "")).startswith("http"):
                    info.registrar_url = link["href"]
                    break
            break
    info.nameservers = sorted({(ns.get("ldhName") or "").lower().rstrip(".") for ns in data.get("nameservers", []) or []
                               if ns.get("ldhName")})
    return info


def rdap_lookup(domain: str) -> DomainInfo:
    tld = domain.rsplit(".", 1)[-1].lower()
    bases = rdap_bootstrap().get(tld)
    if not bases:
        raise LookupUnavailable(f"No RDAP service is published for .{tld}")
    last = None
    for base in bases:
        url = base.rstrip("/") + "/domain/" + domain
        try:
            status, data = _get_json(url)
        except LookupError_ as e:
            last = e
            continue
        if status == 404:
            raise DomainNotFound(f"{domain} is not registered according to {urlsplit(base).netloc}")
        if data is None:
            last = LookupError_(f"RDAP server returned HTTP {status}")
            continue
        return parse_rdap(domain, data, url)
    raise last or LookupError_("RDAP lookup failed")


# ---- WHOIS --------------------------------------------------------------------------------

EXPIRY_RE = re.compile(
    r"^\s*(?:registry expiry date|registrar registration expiration date|expiration date|expiry date|"
    r"expire date|expires on|expires|expiration time|paid-till|valid until|renewal date)\s*[:.]\s*(.+?)\s*$",
    re.I | re.M)
CREATED_RE = re.compile(r"^\s*(?:creation date|created on|created|registered on|registration time)\s*[:.]\s*(.+?)\s*$",
                        re.I | re.M)
REGISTRAR_RE = re.compile(r"^\s*(?:registrar|sponsoring registrar|registrar name)\s*:\s*(.+?)\s*$", re.I | re.M)
NS_RE = re.compile(r"^\s*(?:name server|nserver|nameserver)s?\s*:\s*(\S+)", re.I | re.M)
NOT_FOUND_RE = re.compile(r"^\s*(no match|not found|no data found|no entries found|status:\s*free|domain not found)",
                          re.I | re.M)


def whois_query(server: str, query: str, timeout: float = 15) -> str:
    port = get_settings().whois_port
    try:
        sock = _connect(server, port, timeout)
    except (BlockedTargetError, DnsResolutionError, OSError) as e:
        raise LookupError_(f"WHOIS server {server} unreachable: {e}") from None
    chunks, total = [], 0
    with sock:
        sock.settimeout(timeout)
        try:
            sock.sendall(query.encode("idna") + b"\r\n")
            while total < MAX_WHOIS_BYTES:
                data = sock.recv(8192)
                if not data:
                    break
                chunks.append(data)
                total += len(data)
        except (OSError, socket.timeout) as e:
            raise LookupError_(f"WHOIS query to {server} failed: {e}") from None
    return b"".join(chunks).decode("utf-8", "replace")


def parse_whois(domain: str, text: str, server: str) -> DomainInfo:
    if NOT_FOUND_RE.search(text):
        raise DomainNotFound(f"{domain} is not registered according to {server}")
    info = DomainInfo(domain=domain, source="whois", reference=server)
    m = EXPIRY_RE.search(text)
    if m:
        info.expires_at = _parse_date(m.group(1))
    m = CREATED_RE.search(text)
    if m:
        info.registered_at = _parse_date(m.group(1))
    m = REGISTRAR_RE.search(text)
    if m and not m.group(1).lower().startswith(("whois", "url", "iana")):
        info.registrar = m.group(1)[:300]
    info.nameservers = sorted({n.lower().rstrip(".") for n in NS_RE.findall(text)})[:20]
    return info


def whois_lookup(domain: str) -> DomainInfo:
    s = get_settings()
    if not s.whois_enabled:
        raise LookupUnavailable("WHOIS lookups are disabled (WHOIS_ENABLED=false)")
    tld = domain.rsplit(".", 1)[-1].lower()
    referral = whois_query(s.whois_server_iana, tld)
    m = re.search(r"^\s*(?:whois|refer)\s*:\s*(\S+)", referral, re.I | re.M)
    if not m:
        raise LookupUnavailable(f"No WHOIS server is published for .{tld}")
    server = m.group(1).strip()
    return parse_whois(domain, whois_query(server, domain), server)


def lookup_domain(domain: str) -> DomainInfo:
    """RDAP first; WHOIS only when RDAP has no service for the TLD or returns no expiry date."""
    rdap_error: str | None = None
    rdap_info: DomainInfo | None = None
    try:
        rdap_info = rdap_lookup(domain)
        if rdap_info.expires_at is not None:
            return rdap_info
        rdap_error = "RDAP response did not include an expiry date"
    except DomainNotFound:
        raise
    except LookupUnavailable as e:
        rdap_error = str(e)
    except LookupError_ as e:
        rdap_error = str(e)
    try:
        info = whois_lookup(domain)
    except DomainNotFound:
        raise
    except LookupError_ as e:  # includes LookupUnavailable
        if rdap_info is not None:
            return rdap_info  # registrar/nameservers are real; expiry stays unknown
        raise LookupError_(f"{rdap_error}; WHOIS: {e}") from None
    if info.expires_at is None and rdap_info is not None:
        return rdap_info
    return info
