"""Logging (JSON by default), request IDs, optional Sentry, and production config checks."""
import contextvars
import json
import logging
import sys
import time
import uuid

from app.config import Settings, get_settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_configured = False


class JsonFormatter(logging.Formatter):
    RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            out["request_id"] = rid
        for k, v in record.__dict__.items():  # structured extras: log.info("...", extra={...})
            if k not in self.RESERVED and not k.startswith("_"):
                out[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return
    s = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if s.log_format == "json"
                         else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def setup_sentry(component: str) -> bool:
    s = get_settings()
    if not s.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("sitewatch").warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False
    sentry_sdk.init(dsn=s.sentry_dsn, environment=s.sentry_environment or s.env,
                    traces_sample_rate=s.sentry_traces_sample_rate,
                    send_default_pii=False,  # never send cookies, user emails or request bodies
                    server_name=component)
    return True


class ConfigError(RuntimeError):
    pass


def check_production_settings(s: Settings | None = None) -> list[str]:
    """Refuse to start with unsafe production settings; return warnings for weaker ones."""
    s = s or get_settings()
    if not s.is_production:
        return []
    errors, warnings = [], []
    if not s.cookie_secure:
        errors.append("COOKIE_SECURE must be true in production (the app is served over HTTPS).")
    if "change-me" in s.database_url:
        errors.append("DATABASE_URL still contains the example password 'change-me'.")
    if s.private_target_allowlist:
        warnings.append("PRIVATE_TARGET_ALLOWLIST is set but ignored in production.")
    if not s.encryption_key:
        warnings.append("ENCRYPTION_KEY is empty: hosting provider credentials cannot be stored.")
    if not s.smtp_host:
        warnings.append("SMTP_HOST is empty: alerts and reports cannot be emailed (they are logged as failed).")
    if not s.app_base_url.startswith("https://"):
        warnings.append("APP_BASE_URL should be the public https:// address (used in email links).")
    if errors:
        raise ConfigError(" ".join(errors))
    return warnings


def new_request_id(incoming: str | None) -> str:
    if incoming and len(incoming) <= 64 and all(c.isalnum() or c in "-_." for c in incoming):
        return incoming
    return uuid.uuid4().hex
