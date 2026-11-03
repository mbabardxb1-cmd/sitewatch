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

    # SSRF guard exception for local testing ONLY: IPs/CIDRs (e.g. ["127.0.0.1/32"]) that may be
    # monitored even though they are private. Always ignored when env=production.
    private_target_allowlist: list[str] = []

    # Background jobs
    celery_eager: bool = False  # run tasks inline (tests only)
    dispatch_interval_s: float = 15.0
    uptime_check_retention_days: int = 400  # raw checks; hourly rollups are kept indefinitely

    # Uptime monitoring
    retry_delay_s: int = 30  # delay between retries of a failed check before confirming an outage
    degraded_alert_after: int = 3  # consecutive slow checks before a degradation alert
    server_error_alert_threshold: int = 3  # 5xx responses within the window that trigger an alert
    server_error_window_minutes: int = 60
    max_check_gap_factor: float = 2.5  # gaps longer than interval x factor count as "unknown" time
    check_now_cooldown_s: int = 30

    # Domain / hosting / SSL expiry
    rdap_bootstrap_url: str = "https://data.iana.org/rdap/dns.json"
    whois_enabled: bool = True  # port-43 WHOIS fallback for TLDs without RDAP; see docs on terms of use
    whois_server_iana: str = "whois.iana.org"
    whois_port: int = 43
    domain_alert_days: list[int] = [60, 30, 14, 7]
    hosting_alert_days: list[int] = [60, 30, 14, 7]
    ssl_alert_days: list[int] = [30, 14, 7, 3, 1]

    # Files (report PDFs, uploaded logo). Must be persistent storage (a volume in Docker).
    storage_dir: str = "./storage"

    # Email (SMTP). Leave SMTP_HOST empty to disable sending (notifications are logged as failed).
    notifier_backend: str = "smtp"  # smtp | memory (tests)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str = "SiteWatch <alerts@example.com>"
    app_base_url: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
