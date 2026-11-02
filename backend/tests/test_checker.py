"""Uptime probe + SSRF runtime protections, against real local servers."""
import pytest

from app.monitoring.checker import run_check
from app.netsafe import http as nethttp
from tests.httpfixtures import LocalSite


@pytest.fixture
def server(settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]  # only the test server's address
    with LocalSite() as s:
        yield s


def check(url, timeout=3, codes=(200,), degraded_ms=3000):
    return run_check(url, timeout, list(codes), degraded_ms)


def test_ok(server):
    r = check(server.base + "/")
    assert r.ok and r.http_status == 200 and r.response_ms is not None and not r.degraded


@pytest.mark.parametrize("mode, code", [("500", 500), ("503", 503), ("404", 404)])
def test_unexpected_status_codes(server, mode, code):
    server.state["mode"] = mode
    r = check(server.base + "/")
    assert not r.ok and r.http_status == code and r.error_type == "http_status"
    assert r.is_server_error == (code >= 500)


def test_expected_codes_configurable(server):
    server.state["mode"] = "404"
    assert check(server.base + "/", codes=(200, 404)).ok


def test_redirect_followed_and_final_url_recorded(server):
    r = check(server.base + "/redirect")
    assert r.ok and r.final_url == server.base + "/ok"


def test_redirect_loop_and_long_chain(server):
    assert check(server.base + "/loop-a").error_type == "too_many_redirects"
    r = check(server.base + "/chain/0")
    assert r.error_type == "too_many_redirects" and "More than 5" in r.error_detail


@pytest.mark.parametrize("path", ["/to-metadata", "/to-private", "/to-file"])
def test_redirect_to_unsafe_destination_blocked(server, path):
    r = check(server.base + path)
    assert not r.ok and r.error_type == "blocked"


def test_degraded_when_slow(server):
    server.state.update(mode="slow", delay=0.4)
    r = check(server.base + "/", degraded_ms=200)
    assert r.ok and r.degraded and r.response_ms >= 400


def test_timeout(server):
    r = check(server.base + "/hang", timeout=1)
    assert not r.ok and r.error_type == "timeout"


def test_connection_refused(server):
    port = server.httpd.server_port
    server.httpd.shutdown()
    server.httpd.server_close()
    r = check(f"http://127.0.0.1:{port}/")
    assert not r.ok and r.error_type == "connect"


def test_ssl_error_self_signed(settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    with LocalSite(tls=True) as s:
        r = check(s.base + "/")
    assert not r.ok and r.error_type == "ssl"


def test_loopback_blocked_without_allowlist():
    with LocalSite() as s:
        r = check(s.base + "/")
    assert not r.ok and r.error_type == "blocked"


def test_dns_rebinding_blocked(monkeypatch, server):
    """A public-looking hostname that resolves to a private address must be refused at connect time."""
    monkeypatch.setattr(nethttp, "resolve_host", lambda host, port: ["169.254.169.254"])
    r = check("http://innocent.example.com/")
    assert r.error_type == "blocked" and "169.254.169.254" in r.error_detail


def test_any_private_answer_blocks(monkeypatch, server):
    monkeypatch.setattr(nethttp, "resolve_host", lambda host, port: ["93.184.215.14", "10.0.0.7"])
    assert check("http://mixed.example.com/").error_type == "blocked"


def test_dns_failure(monkeypatch):
    def fail(host, port):
        raise nethttp.DnsResolutionError(f"DNS lookup failed for {host}")
    monkeypatch.setattr(nethttp, "resolve_host", fail)
    assert check("http://nonexistent.example.com/").error_type == "dns"


def test_pinned_connection_uses_validated_ip(monkeypatch, server):
    """The socket must connect to the IP we validated, with the original Host header."""
    port = server.httpd.server_port
    monkeypatch.setattr(nethttp, "resolve_host", lambda host, p: ["127.0.0.1"])
    r = check(f"http://pinned.example.com:{port}/ok")
    assert r.ok and r.final_url == f"http://pinned.example.com:{port}/ok"


def test_env_proxy_ignored(monkeypatch, server):
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.9.9.9:3128")
    assert check(server.base + "/").ok
