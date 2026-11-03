import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    audits,
    auth,
    clients,
    dashboard,
    expiry,
    health,
    monitoring,
    notifications,
    reports,
    settings,
    users,
    websites,
)
from app.config import get_settings
from app.observability import check_production_settings, new_request_id, request_id_var, setup_logging, setup_sentry

log = logging.getLogger("sitewatch")
access_log = logging.getLogger("sitewatch.access")


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging()
    for w in check_production_settings(s):  # raises ConfigError on unsafe production settings
        log.warning("Configuration warning: %s", w)
    setup_sentry("api")
    app = FastAPI(
        title="SiteWatch API",
        version="0.1.0",
        docs_url=None if s.is_production else "/api/docs",
        openapi_url=None if s.is_production else "/api/openapi.json",
    )
    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
            allow_methods=["*"], allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = new_request_id(request.headers.get("x-request-id"))
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            # Path only: query strings may carry tokens or personal data.
            access_log.info("request", extra={"method": request.method, "path": request.url.path, "status": status,
                                              "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                                              "client_ip": request.client.host if request.client else None})
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    app.include_router(health.router, prefix="/api")
    for r in (auth.router, users.router, clients.router, websites.router, dashboard.router, monitoring.router,
              notifications.router, audits.router, expiry.router, reports.router, settings.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
