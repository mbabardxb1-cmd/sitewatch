"""A single uptime probe. Pure I/O; no database access."""
import ssl
from dataclasses import dataclass

import httpx

from app.netsafe.http import (
    BlockedTargetError,
    DnsResolutionError,
    TooManyRedirects,
    UnsafeUrlError,
    safe_client,
    safe_fetch,
)


@dataclass
class CheckResult:
    ok: bool
    http_status: int | None = None
    response_ms: int | None = None
    error_type: str | None = None  # timeout|dns|connect|ssl|http_status|too_many_redirects|blocked|other
    error_detail: str | None = None
    final_url: str | None = None
    degraded: bool = False

    @property
    def is_server_error(self) -> bool:
        return self.http_status is not None and self.http_status >= 500

    def summary(self) -> str:
        if self.ok:
            return f"HTTP {self.http_status} in {self.response_ms} ms"
        if self.error_type == "http_status":
            return f"Unexpected HTTP {self.http_status}"
        return {
            "timeout": "Timed out",
            "dns": "DNS lookup failed",
            "connect": "Connection failed",
            "ssl": "SSL/TLS error",
            "too_many_redirects": "Redirect loop or too many redirects",
            "blocked": "Destination blocked by security policy",
        }.get(self.error_type or "", "Request failed")


def _root_cause(exc: BaseException) -> BaseException:
    seen = set()
    while exc.__cause__ is not None or exc.__context__ is not None:
        nxt = exc.__cause__ or exc.__context__
        if id(nxt) in seen:
            break
        seen.add(id(nxt))
        exc = nxt
    return exc


def _is_ssl_error(exc: BaseException) -> bool:
    e: BaseException | None = exc
    while e is not None:
        if isinstance(e, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in type(e).__name__:
            return True
        e = e.__cause__ or e.__context__
    return False


def run_check(url: str, timeout_s: float, expected_codes: list[int], degraded_threshold_ms: int) -> CheckResult:
    try:
        with safe_client(timeout_s) as client:
            res = safe_fetch(client, url)
    except (BlockedTargetError, UnsafeUrlError) as e:
        return CheckResult(ok=False, error_type="blocked", error_detail=str(e)[:500])
    except DnsResolutionError as e:
        return CheckResult(ok=False, error_type="dns", error_detail=str(e)[:500])
    except TooManyRedirects as e:
        chain = " -> ".join(f"{h.status} {h.url}" for h in e.hops)
        return CheckResult(ok=False, error_type="too_many_redirects", error_detail=f"{e}: {chain}"[:1000])
    except httpx.TimeoutException as e:
        return CheckResult(ok=False, error_type="timeout",
                           error_detail=f"No response within {timeout_s:g}s ({type(e).__name__})")
    except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
        if _is_ssl_error(e):
            return CheckResult(ok=False, error_type="ssl", error_detail=str(_root_cause(e))[:500])
        return CheckResult(ok=False, error_type="connect", error_detail=str(_root_cause(e))[:500] or type(e).__name__)
    except httpx.HTTPError as e:
        return CheckResult(ok=False, error_type="other", error_detail=f"{type(e).__name__}: {e}"[:500])

    if res.status not in expected_codes:
        return CheckResult(ok=False, http_status=res.status, response_ms=res.elapsed_ms, error_type="http_status",
                           error_detail=f"Expected {', '.join(map(str, expected_codes))}; got {res.status}",
                           final_url=res.final_url)
    return CheckResult(ok=True, http_status=res.status, response_ms=res.elapsed_ms, final_url=res.final_url,
                       degraded=res.elapsed_ms > degraded_threshold_ms)
