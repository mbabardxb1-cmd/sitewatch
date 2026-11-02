from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.auth.ratelimit import get_redis
from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(response: Response):
    checks: dict[str, str] = {}
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001
        checks["redis"] = "error"
    ok = all(v == "ok" for v in checks.values())
    # Background workers are reported but do not fail API readiness (the API works without them).
    workers: dict[str, float | None] = {"worker_heartbeat_age_s": None, "last_dispatch_age_s": None}
    try:
        import time

        from app.worker.tasks import BEAT_KEY, HEARTBEAT_KEY

        r = get_redis()
        for key, name in ((HEARTBEAT_KEY, "worker_heartbeat_age_s"), (BEAT_KEY, "last_dispatch_age_s")):
            v = r.get(key)
            workers[name] = round(time.time() - float(v), 1) if v else None
    except Exception:  # noqa: BLE001
        pass
    checks_out = {**checks, **workers}
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks_out}
