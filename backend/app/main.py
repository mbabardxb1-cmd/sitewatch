import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import audits, auth, clients, dashboard, expiry, health, monitoring, notifications, users, websites
from app.config import get_settings

log = logging.getLogger("sitewatch")


def create_app() -> FastAPI:
    s = get_settings()
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
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
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
              notifications.router, audits.router, expiry.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
