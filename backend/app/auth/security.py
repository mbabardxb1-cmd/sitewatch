"""Password hashing and token helpers."""
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with library-recommended parameters
# Used to equalize timing when the user does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_password_strength(password: str, min_length: int) -> str | None:
    """Return an error message, or None if acceptable."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters."
    if len(password) > 256:
        return "Password is too long."
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        return "Password must contain upper- and lower-case letters and a digit."
    return None
