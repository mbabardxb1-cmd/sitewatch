"""Hosting-provider integrations.

Hosting renewal dates are generally NOT public. They can only be verified through a provider API
the customer has authorised; otherwise they are entered manually and labelled as such.

To add a provider, implement `HostingProvider` and call `register()` at import time, e.g. in
`app/expiry/providers/<name>.py`, then import that module in `app/expiry/providers/__init__.py`.
No providers ship by default (by decision in phase 5): manual entry is always available.
"""
import json
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@dataclass
class HostingLookup:
    renewal_date: date | None
    plan: str | None = None
    provider_name: str | None = None


class HostingProviderError(Exception):
    pass


class HostingProvider(Protocol):
    key: str
    display_name: str
    credential_fields: list[str]  # names of required secret fields, e.g. ["api_token"]

    def fetch(self, credentials: dict, account_reference: str | None) -> HostingLookup: ...


REGISTRY: dict[str, HostingProvider] = {}


def register(provider: HostingProvider) -> None:
    REGISTRY[provider.key] = provider


class CredentialsUnavailable(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise CredentialsUnavailable("ENCRYPTION_KEY is not configured; provider credentials cannot be stored.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise CredentialsUnavailable(f"ENCRYPTION_KEY is invalid: {e}") from None


def encrypt_credentials(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt_credentials(blob: bytes) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob))
    except InvalidToken:
        raise CredentialsUnavailable("Stored credentials cannot be decrypted with the current ENCRYPTION_KEY.") from None
