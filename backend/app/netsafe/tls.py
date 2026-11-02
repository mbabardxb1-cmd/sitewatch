"""TLS certificate inspection through the same SSRF guard as HTTP checks."""
import ipaddress
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime

import certifi
from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

from app.netsafe import http as nethttp
from app.netsafe.urls import ip_permitted


@dataclass
class CertInfo:
    hostname: str
    valid: bool  # chain + hostname verified and currently within validity window
    error: str | None = None
    issuer: str | None = None
    subject: str | None = None
    san: list[str] = field(default_factory=list)
    not_before: datetime | None = None
    not_after: datetime | None = None

    def days_remaining(self, now: datetime | None = None) -> int | None:
        if self.not_after is None:
            return None
        return int(((self.not_after - (now or datetime.now(UTC))).total_seconds()) // 86400)


def _name(n: x509.Name) -> str:
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
        attrs = n.get_attributes_for_oid(oid)
        if attrs:
            return str(attrs[0].value)
    return n.rfc4514_string()


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        ips = nethttp.resolve_host(host, port)
    bad = [ip for ip in ips if not ip_permitted(ip)]
    if bad:
        raise nethttp.BlockedTargetError(f"{host} resolves to a disallowed address ({bad[0]})")
    last: Exception | None = None
    for ip in ips:
        try:
            return socket.create_connection((ip, port), timeout=timeout)
        except OSError as e:
            last = e
    raise last or OSError("connection failed")


def inspect_certificate(host: str, port: int = 443, timeout: float = 10) -> CertInfo:
    """Return certificate details. Raises BlockedTargetError / DnsResolutionError / OSError
    when no TLS handshake could be attempted at all (the check could not be performed)."""
    verify_error: str | None = None
    ctx = ssl.create_default_context(cafile=certifi.where())
    raw = _connect(host, port, timeout)
    try:
        with ctx.wrap_socket(raw, server_hostname=host) as s:
            der = s.getpeercert(binary_form=True)
    except ssl.SSLCertVerificationError as e:
        verify_error = e.verify_message or str(e)
        der = None
    except ssl.SSLError as e:
        return CertInfo(hostname=host, valid=False, error=f"TLS handshake failed: {e.reason or e}")
    if der is None:
        # Fetch the certificate without verification purely to report its details.
        raw = _connect(host, port, timeout)
        nv = ssl.create_default_context()
        nv.check_hostname = False
        nv.verify_mode = ssl.CERT_NONE
        try:
            with nv.wrap_socket(raw, server_hostname=host) as s:
                der = s.getpeercert(binary_form=True)
        except ssl.SSLError as e:
            return CertInfo(hostname=host, valid=False, error=verify_error or str(e))
    cert = x509.load_der_x509_certificate(der)
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(
            x509.DNSName)
    except x509.ExtensionNotFound:
        san = []
    info = CertInfo(hostname=host, valid=verify_error is None, error=verify_error, issuer=_name(cert.issuer),
                    subject=_name(cert.subject), san=list(san)[:50], not_before=cert.not_valid_before_utc,
                    not_after=cert.not_valid_after_utc)
    now = datetime.now(UTC)
    if info.valid and info.not_after and info.not_after < now:
        info.valid, info.error = False, "Certificate has expired"
    return info
