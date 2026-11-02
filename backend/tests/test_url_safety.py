import pytest

from app.netsafe.urls import UnsafeUrlError, normalize_site_url


@pytest.mark.parametrize("raw, expected", [
    ("example.com", ("https://example.com/", "example.com")),
    ("HTTP://Example.COM", ("http://example.com/", "example.com")),
    ("https://example.com:443/path?q=1#frag", ("https://example.com/path?q=1", "example.com")),
    ("https://example.com:8443/", ("https://example.com:8443/", "example.com")),
    ("https://bücher.de", ("https://xn--bcher-kva.de/", "xn--bcher-kva.de")),
    ("https://example.com./", ("https://example.com/", "example.com")),
    ("http://93.184.215.14/", ("http://93.184.215.14/", "93.184.215.14")),
])
def test_valid_urls_normalized(raw, expected):
    assert normalize_site_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "ftp://example.com", "file:///etc/passwd", "javascript:alert(1)", "gopher://example.com",
    "https://user:pass@example.com", "https://localhost", "https://foo.localhost", "http://LOCALHOST:8080",
    "http://127.0.0.1", "http://10.0.0.5", "http://172.16.1.1", "http://192.168.1.1", "http://169.254.169.254",
    "http://[::1]", "http://[fe80::1]", "http://[fd00::1]", "http://[::ffff:127.0.0.1]", "http://0.0.0.0",
    "http://100.64.0.1", "http://metadata.google.internal", "https://printer.local", "https://intranet",
    "https://example.com:99999", "https://" + "a" * 2050 + ".com",
])
def test_unsafe_urls_rejected(raw):
    with pytest.raises(UnsafeUrlError):
        normalize_site_url(raw)


def test_private_allowlist_only_outside_production(settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    assert normalize_site_url("http://127.0.0.1:8080")[1] == "127.0.0.1"
    with pytest.raises(UnsafeUrlError):  # only the listed network is opened up
        normalize_site_url("http://10.0.0.1")
    with pytest.raises(UnsafeUrlError):  # names are never allowlisted
        normalize_site_url("http://localhost:8080")
    settings.env = "production"
    with pytest.raises(UnsafeUrlError):
        normalize_site_url("http://127.0.0.1:8080")
