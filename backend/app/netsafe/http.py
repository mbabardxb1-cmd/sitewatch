"""SSRF-safe outbound HTTP (runtime layer).

* DNS is resolved by us at connect time; if ANY resolved address is private/loopback/
  link-local/metadata/etc. the connection is refused (unless allowlisted for local tests).
* The socket connects to the exact IP that was validated, so a second DNS answer cannot
  redirect it (DNS rebinding). TLS still uses the original hostname for SNI and certificate
  verification.
* Redirects are never followed automatically: each hop's URL is re-validated (scheme, host
  rules) and its connection goes through the same IP checks.
* Environment proxies are ignored (trust_env=False), so traffic cannot be sent to a proxy
  that would bypass these checks.
"""
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import certifi
import httpcore
import httpx

from app.netsafe.urls import UnsafeUrlError, ip_permitted, normalize_site_url

USER_AGENT = "SiteWatchMonitor/1.0 (+uptime monitoring)"
MAX_REDIRECTS = 5


class BlockedTargetError(Exception):
    """Destination resolved to an address we refuse to contact."""


class DnsResolutionError(Exception):
    pass


def resolve_host(host: str, port: int) -> list[str]:
    """Return the IP addresses for host. Separate function so tests can substitute it."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise DnsResolutionError(f"DNS lookup failed for {host}: {e.strerror or e}") from None
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise DnsResolutionError(f"DNS lookup for {host} returned no addresses")
    return ips


class SafeNetworkBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            ips = resolve_host(host, port)
        bad = [ip for ip in ips if not ip_permitted(ip)]
        if bad:
            raise BlockedTargetError(f"{host} resolves to a disallowed address ({bad[0]})")
        last: Exception | None = None
        for ip in ips:
            try:
                return super().connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                           socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        assert last is not None
        raise last


class SafeTransport(httpx.HTTPTransport):
    def __init__(self, verify: bool = True):
        super().__init__()
        ctx = ssl.create_default_context(cafile=certifi.where())
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._pool = httpcore.ConnectionPool(ssl_context=ctx, network_backend=SafeNetworkBackend(),
                                             max_connections=10, retries=0)


def safe_client(timeout: float, verify: bool = True) -> httpx.Client:
    return httpx.Client(transport=SafeTransport(verify=verify), timeout=httpx.Timeout(timeout),
                        follow_redirects=False, trust_env=False, headers={"User-Agent": USER_AGENT})


@dataclass
class Hop:
    url: str
    status: int


@dataclass
class FetchResult:
    status: int
    final_url: str
    elapsed_ms: int  # time to first response headers of the final hop, summed over hops
    headers: httpx.Headers
    body: bytes = b""
    hops: list[Hop] = field(default_factory=list)


class TooManyRedirects(Exception):
    def __init__(self, hops: list[Hop], loop: bool):
        super().__init__("Redirect loop" if loop else f"More than {MAX_REDIRECTS} redirects")
        self.hops = hops
        self.loop = loop


def safe_fetch(client: httpx.Client, url: str, method: str = "GET", read_body_bytes: int = 0,
               max_redirects: int = MAX_REDIRECTS, headers: dict | None = None) -> FetchResult:
    """Fetch url, following redirects manually with re-validation of every hop.

    Raises UnsafeUrlError, BlockedTargetError, DnsResolutionError, TooManyRedirects or httpx errors.
    """
    hops: list[Hop] = []
    seen: set[str] = set()
    total_ms = 0
    current = url
    while True:
        normalized, _ = normalize_site_url(current)  # re-validate every hop
        if normalized in seen:
            raise TooManyRedirects(hops, loop=True)
        seen.add(normalized)
        start = time.perf_counter()
        with client.stream(method, normalized, headers=headers) as resp:
            total_ms += int((time.perf_counter() - start) * 1000)
            body = b""
            if read_body_bytes and not resp.is_redirect:
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) >= read_body_bytes:
                        body = body[:read_body_bytes]
                        break
            hops.append(Hop(normalized, resp.status_code))
            location = resp.headers.get("location")
            if resp.is_redirect and location:
                if len(hops) > max_redirects:
                    raise TooManyRedirects(hops, loop=False)
                current = urljoin(normalized, location)
                continue
            return FetchResult(status=resp.status_code, final_url=normalized, elapsed_ms=total_ms,
                               headers=resp.headers, body=body, hops=hops)


__all__ = ["BlockedTargetError", "DnsResolutionError", "FetchResult", "TooManyRedirects", "UnsafeUrlError",
           "resolve_host", "safe_client", "safe_fetch"]
