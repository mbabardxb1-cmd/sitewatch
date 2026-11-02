"""Application configuration, loaded from environment variables (see .env.example)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field(default="development", description="development | test | production")
    database_url: str = "postgresql+psycopg://sitewatch:sitewatch@localhost:5432/sitewatch"
    redis_url: str = "redis://localhost:6379/0"

    # Sessions
    session_cookie_name: str = "sw_session"
    csrf_cookie_name: str = "sw_csrf"
    session_ttl_hours: int = 12
    cookie_secure: bool = True  # must stay True in production (HTTPS)

    # Login protection
    login_max_attempts_per_ip: int = 20  # per window
    login_max_attempts_per_email: int = 5  # per window
    login_window_seconds: int = 900
    account_lockout_threshold: int = 10  # consecutive failures
    account_lockout_minutes: int = 15
    password_min_length: int = 12

    # Encryption key for stored third-party credentials (Fernet, urlsafe base64, 32 bytes)
    encryption_key: str = ""

    cors_origins: list[str] = []

    # SSRF guard override for local testing ONLY (e.g. monitoring a site on localhost).
    # Ignored when env=production.
    allow_private_targets: bool = False

    @property
    def private_targets_allowed(self) -> bool:
        return self.allow_private_targets and not self.is_production

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
