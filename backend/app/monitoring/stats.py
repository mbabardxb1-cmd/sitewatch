"""Uptime percentages and response-time series, computed from stored checks and incidents.

Method (documented so reports can state it):
* Each check "covers" the time until the next check, capped at interval x max_check_gap_factor.
  Time not covered by any check (worker stopped, monitoring paused, site newly added) is
  UNKNOWN and excluded — it never counts as uptime.
* Downtime is the overlap of confirmed incidents (first failed check -> recovery) with the period.
* uptime % = (monitored - downtime) / monitored. No monitored time -> None ("No data").
"""
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings


@dataclass
class UptimeStats:
    period_start: datetime
    period_end: datetime
    uptime_pct: float | None
    monitored_s: int
    downtime_s: int
    unknown_s: int
    coverage_pct: float
    checks: int
    incidents: int

    def as_dict(self) -> dict:
        return asdict(self)


COVERAGE_SQL = text("""
WITH c AS (
    SELECT uc.website_id, uc.checked_at,
           LEAD(uc.checked_at) OVER (PARTITION BY uc.website_id ORDER BY uc.checked_at) AS nxt,
           w.check_interval_s
    FROM uptime_checks uc JOIN websites w ON w.id = uc.website_id
    WHERE uc.website_id = ANY(:ids)
      AND uc.checked_at >= CAST(:start AS timestamptz) - make_interval(secs => w.check_interval_s * :factor)
      AND uc.checked_at < :end
)
SELECT website_id,
       COALESCE(SUM(GREATEST(0, EXTRACT(EPOCH FROM (
           LEAST(COALESCE(nxt, CAST(:now AS timestamptz)),
                 checked_at + make_interval(secs => check_interval_s * :factor),
                 CAST(:end AS timestamptz))
           - GREATEST(checked_at, CAST(:start AS timestamptz))
       )))), 0) AS covered,
       COUNT(*) FILTER (WHERE checked_at >= :start) AS n_checks
FROM c GROUP BY website_id
""")

DOWNTIME_SQL = text("""
SELECT website_id,
       COALESCE(SUM(GREATEST(0, EXTRACT(EPOCH FROM (
           LEAST(COALESCE(ended_at, CAST(:now AS timestamptz)), CAST(:end AS timestamptz))
           - GREATEST(started_at, CAST(:start AS timestamptz))
       )))), 0) AS down,
       COUNT(*) AS n
FROM uptime_incidents
WHERE website_id = ANY(:ids) AND started_at < :end AND COALESCE(ended_at, CAST(:now AS timestamptz)) > :start
GROUP BY website_id
""")


def uptime_for_sites(db: Session, ids: list[uuid.UUID], start: datetime, end: datetime,
                     now: datetime | None = None) -> dict[uuid.UUID, UptimeStats]:
    now = now or datetime.now(UTC)
    end = min(end, now)
    period_s = max(0, int((end - start).total_seconds()))
    out: dict[uuid.UUID, UptimeStats] = {}
    if not ids:
        return out
    params = {"ids": list(ids), "start": start, "end": end, "now": now, "factor": get_settings().max_check_gap_factor}
    cov = {r.website_id: (float(r.covered), int(r.n_checks)) for r in db.execute(COVERAGE_SQL, params)}
    down = {r.website_id: (float(r.down), int(r.n)) for r in db.execute(DOWNTIME_SQL, params)}
    for wid in ids:
        covered, n_checks = cov.get(wid, (0.0, 0))
        down_s, n_inc = down.get(wid, (0.0, 0))
        monitored = int(round(covered))
        down_s = int(round(min(down_s, covered)))
        out[wid] = UptimeStats(
            period_start=start, period_end=end,
            uptime_pct=round((monitored - down_s) / monitored * 100, 3) if monitored > 0 else None,
            monitored_s=monitored, downtime_s=down_s, unknown_s=max(0, period_s - monitored),
            coverage_pct=round(monitored / period_s * 100, 1) if period_s else 0.0,
            checks=n_checks, incidents=n_inc,
        )
    return out


def standard_periods(now: datetime | None = None) -> dict[str, tuple[datetime, datetime]]:
    now = now or datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "day": (now - timedelta(days=1), now),
        "week": (now - timedelta(days=7), now),
        "month": (now - timedelta(days=30), now),
        "calendar_month": (month_start, now),
    }


def aggregate(stats: list[UptimeStats]) -> float | None:
    monitored = sum(s.monitored_s for s in stats)
    if monitored <= 0:
        return None
    return round((monitored - sum(s.downtime_s for s in stats)) / monitored * 100, 3)


RESPONSE_SQL = text("""
SELECT date_trunc('hour', checked_at) AS bucket,
       COUNT(*) AS checks,
       COUNT(*) FILTER (WHERE ok) AS successes,
       ROUND(AVG(response_ms) FILTER (WHERE ok)) AS avg_ms,
       ROUND(CAST(percentile_cont(0.95) WITHIN GROUP (ORDER BY response_ms) FILTER (WHERE ok) AS numeric)) AS p95_ms,
       MAX(response_ms) FILTER (WHERE ok) AS max_ms
FROM uptime_checks
WHERE website_id = :id AND checked_at >= :since
GROUP BY 1 ORDER BY 1
""")

ROLLUP_SERIES_SQL = text("""
SELECT bucket_start AS bucket, checks, successes, avg_ms, p95_ms, max_ms
FROM performance_metrics WHERE website_id = :id AND bucket_start >= :since ORDER BY 1
""")


def response_series(db: Session, website_id: uuid.UUID, hours: int) -> list[dict]:
    """Hourly response-time buckets. Uses raw checks, falling back to rollups for older hours
    whose raw checks were removed by retention."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = {r.bucket: r for r in db.execute(ROLLUP_SERIES_SQL, {"id": website_id, "since": since})}
    rows.update({r.bucket: r for r in db.execute(RESPONSE_SQL, {"id": website_id, "since": since})})
    return [
        {"bucket": b, "checks": r.checks, "successes": r.successes,
         "avg_ms": int(r.avg_ms) if r.avg_ms is not None else None,
         "p95_ms": int(r.p95_ms) if r.p95_ms is not None else None,
         "max_ms": int(r.max_ms) if r.max_ms is not None else None}
        for b, r in sorted(rows.items())
    ]


ROLLUP_UPSERT_SQL = text("""
INSERT INTO performance_metrics (website_id, bucket_start, checks, successes, avg_ms, p95_ms, max_ms)
SELECT website_id, date_trunc('hour', checked_at), COUNT(*), COUNT(*) FILTER (WHERE ok),
       ROUND(AVG(response_ms) FILTER (WHERE ok)),
       ROUND(CAST(percentile_cont(0.95) WITHIN GROUP (ORDER BY response_ms) FILTER (WHERE ok) AS numeric)),
       MAX(response_ms) FILTER (WHERE ok)
FROM uptime_checks
WHERE checked_at >= :since AND checked_at < :until
GROUP BY 1, 2
ON CONFLICT (website_id, bucket_start) DO UPDATE SET
    checks = EXCLUDED.checks, successes = EXCLUDED.successes, avg_ms = EXCLUDED.avg_ms,
    p95_ms = EXCLUDED.p95_ms, max_ms = EXCLUDED.max_ms
""")


def rollup(db: Session, hours_back: int = 3, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    until = now.replace(minute=0, second=0, microsecond=0)  # only completed hours
    db.execute(ROLLUP_UPSERT_SQL, {"since": until - timedelta(hours=hours_back), "until": until})
