"""Static URL validation for monitored websites (first SSRF layer).

This rejects obviously unsafe targets at registration time. Phase 3 adds the runtime layer:
DNS resolution with IP checks, pinned connections and per-redirect re-validation.
"""
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from app.config import get_settings

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan", ".intranet", ".corp")
# Addresses that are "global" by the stdlib but still unsafe or meaningless as targets.
EXTRA_BLOCKED_NETS = [
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("64:ff9b::/96"),   # NAT64 can map to internal v4
]


class UnsafeUrlError(ValueError):
    pass


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_link_local or ip.is_loopback:
        return True
    return any(ip in net for net in EXTRA_BLOCKED_NETS)


def normalize_site_url(raw: str) -> tuple[str, str]:
    """Validate and normalize a website URL. Returns (normalized_url, hostname).

    Raises UnsafeUrlError with a user-facing message when the URL is not acceptable.
    """
    raw = (raw or "").strip()
    if not raw:
        raise UnsafeUrlError("URL is required.")
    if "://" not in raw:
        raw = "https://" + raw
    if len(raw) > 2048:
        raise UnsafeUrlError("URL is too long.")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as e:
        raise UnsafeUrlError(f"Invalid URL: {e}") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeUrlError("Only http and https URLs can be monitored.")
    if parts.username or parts.password:
        raise UnsafeUrlError("URLs must not contain credentials.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeUrlError("URL must include a hostname.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnsafeUrlError("Invalid hostname.") from None

    allow_private = get_settings().private_targets_allowed
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if is_blocked_ip(ip) and not allow_private:
            raise UnsafeUrlError("Private, loopback and reserved IP addresses cannot be monitored.")
    else:
        if not allow_private and (host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES)):
            raise UnsafeUrlError("Internal hostnames cannot be monitored.")
        if "." not in host and not allow_private:
            raise UnsafeUrlError("Hostname must be a fully qualified domain name.")

    netloc = f"[{host}]" if ip is not None and ip.version == 6 else host
    if port is not None and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        netloc += f":{port}"
    path = parts.path or "/"
    normalized = urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))
    return normalized, host
