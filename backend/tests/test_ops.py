"""Phase 8 operations: settings, retention, logging, config checks, health."""
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditRun,
    NotificationLog,
    PerformanceMetric,
    SystemSetting,
    UptimeCheck,
    UserSession,
)
from app.observability import ConfigError, JsonFormatter, check_production_settings, new_request_id, request_id_var
from app.services.retention import run_retention, setting_int


def test_system_settings_api(as_user, db):
    admin, dev = as_user("super_admin"), as_user("developer")
    assert dev.get("/api/v1/settings/system").status_code == 403
    cur = admin.get("/api/v1/settings/system").json()
    assert cur["values"]["report_generation_day"] == 2 and cur["limits"]["uptime_check_retention_days"]["min"] == 62
    assert admin.put("/api/v1/settings/system", json={"report_generation_day": 31}).status_code == 422
    assert admin.put("/api/v1/settings/system", json={"uptime_check_retention_days": 10}).status_code == 422
    r = admin.put("/api/v1/settings/system", json={"report_generation_day": 5, "audit_run_retention_months": 12})
    assert r.json()["values"]["report_generation_day"] == 5 and setting_int(db, "reports.generation_day") == 5
    log = admin.get("/api/v1/settings/audit-log?action=settings").json()
    assert log[0]["action"] == "settings.system" and log[0]["actor_email"] == admin.user.email
    assert log[0]["detail"] == {"report_generation_day": 5, "audit_run_retention_months": 12}
    assert dev.get("/api/v1/settings/audit-log").status_code == 403


def test_report_schedule_uses_setting(db, make_client, make_website):
    from app.reports.service import schedule_monthly

    site = make_website(make_client(), "S", "https://s.example.com/")
    site.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.add(SystemSetting(key="reports.generation_day", value=10))
    db.commit()
    assert schedule_monthly(db, now=datetime(2026, 11, 9, tzinfo=UTC)) == []
    assert len(schedule_monthly(db, now=datetime(2026, 11, 10, tzinfo=UTC))) == 2


def test_out_of_range_setting_is_clamped(db):
    db.add(SystemSetting(key="retention.uptime_checks_days", value=1))
    db.commit()
    assert setting_int(db, "retention.uptime_checks_days") == 62


def test_retention(db, make_client, make_website, as_user):
    site = make_website(make_client(), "S", "https://s.example.com/")
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    old = now - timedelta(days=500)
    db.add_all([UptimeCheck(website_id=site.id, checked_at=old + timedelta(minutes=5 * i), ok=True, http_status=200,
                            response_ms=100 + i) for i in range(6)])
    db.add(UptimeCheck(website_id=site.id, checked_at=now - timedelta(days=1), ok=True, http_status=200, response_ms=90))
    runs = [AuditRun(website_id=site.id, trigger="manual", status="completed", created_at=now - timedelta(days=d))
            for d in (1200, 900, 800)]
    db.add_all(runs)
    db.add_all([NotificationLog(website_id=site.id, event_type="x", dedup_key="old", channel="email", status="sent",
                                created_at=now - timedelta(days=400)),
                NotificationLog(website_id=site.id, event_type="x", dedup_key="new", channel="email", status="sent",
                                created_at=now - timedelta(days=10))])
    user = as_user().user
    db.add(UserSession(token_hash="h" * 64, csrf_token="c", user_id=user.id, expires_at=now - timedelta(days=40)))
    db.commit()

    out = run_retention(db, now=now)
    assert out["uptime_checks"] == 6 and out["notification_logs"] == 1 and out["sessions"] == 1
    assert out["audit_runs"] == 2  # all old, but the site's latest run is always kept
    assert db.scalar(select(func.count()).select_from(UptimeCheck)) == 1
    m = db.scalar(select(PerformanceMetric))  # history survives as an hourly rollup
    assert m is not None and m.checks == 6 and m.avg_ms == 103
    assert [r.id for r in db.scalars(select(AuditRun))] == [runs[2].id]


def test_production_config_checks(settings):
    settings.env = "production"
    settings.cookie_secure = False
    with pytest.raises(ConfigError, match="COOKIE_SECURE"):
        check_production_settings(settings)
    settings.cookie_secure = True
    settings.database_url = "postgresql+psycopg://u:change-me-long-random@db/x"
    with pytest.raises(ConfigError, match="change-me"):
        check_production_settings(settings)
    settings.database_url = "postgresql+psycopg://u:Str0ng@db/x"
    settings.smtp_host, settings.encryption_key, settings.app_base_url = "", "", "http://x"
    warnings = check_production_settings(settings)
    assert len(warnings) == 3
    settings.env = "development"
    assert check_production_settings(settings) == []


def test_request_id_and_json_logs(api, caplog):
    r = api.get("/api/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    evil = api.get("/api/healthz", headers={"X-Request-ID": "x\nInjected: 1"})
    assert evil.headers["X-Request-ID"] != "x\nInjected: 1" and len(evil.headers["X-Request-ID"]) == 32
    assert new_request_id(None) != new_request_id(None)
    token = request_id_var.set("rid-1")
    try:
        rec = logging.LogRecord("sitewatch.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        rec.status = 200
        line = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "hello world" and line["request_id"] == "rid-1" and line["status"] == 200
    assert line["level"] == "INFO" and line["ts"].endswith("Z")


def test_access_log_omits_query_strings(api, caplog):
    with caplog.at_level(logging.INFO, logger="sitewatch.access"):
        api.get("/api/healthz?token=secret-value")
    rec = next(r for r in caplog.records if r.name == "sitewatch.access")
    assert rec.path == "/api/healthz" and "secret-value" not in json.dumps(rec.__dict__, default=str)


def test_readyz_reports_background_staleness(api):
    body = api.get("/api/readyz").json()
    assert body["checks"]["background"] == "stale"  # no worker/beat in the test run
    from app.auth.ratelimit import get_redis
    from app.worker.tasks import BEAT_KEY, HEARTBEAT_KEY
    import time

    get_redis().set(BEAT_KEY, time.time())
    get_redis().set(HEARTBEAT_KEY, time.time())
    assert api.get("/api/readyz").json()["checks"]["background"] == "ok"


def test_sentry_disabled_without_dsn(settings):
    from app.observability import setup_sentry

    settings.sentry_dsn = ""
    assert setup_sentry("api") is False
