"""Assemble the facts for a monthly report into a JSON-serialisable snapshot.

The PDF is rendered only from this snapshot, which is stored with the report, so a report
can always be explained ("where did this number come from?") and re-rendered identically.

Principles enforced here:
* Every figure comes from stored monitoring/audit data; missing data is reported as such.
* Monitoring results and maintenance work performed are separate sections.
* "Resolved" means verified by a later audit (issue.status == resolved), never self-reported.
* The overall status is a label with its reasons listed, not a score.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.checks import CHECKS
from app.expiry import service as expiry
from app.expiry.lookup import registrable_domain
from app.models import (
    AuditRun,
    Client,
    DomainRecord,
    Issue,
    MaintenanceActivity,
    UptimeIncident,
    Website,
)
from app.monitoring.stats import uptime_for_sites

SEV = ["critical", "high", "medium", "low", "info"]
CATEGORY_LABELS = {"update": "Updates", "fix": "Fixes", "security": "Security", "content": "Content",
                   "performance": "Performance", "backup": "Backups", "other": "Other"}


def period_bounds(period: str) -> tuple[datetime, datetime]:
    y, m = (int(x) for x in period.split("-"))
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
    return start, end


def previous_period(period: str) -> str:
    start, _ = period_bounds(period)
    return (start - timedelta(days=1)).strftime("%Y-%m")


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


DAILY_SQL = text("""
SELECT date_trunc('day', checked_at) AS day, COUNT(*) AS checks, COUNT(*) FILTER (WHERE ok) AS ok,
       ROUND(AVG(response_ms) FILTER (WHERE ok)) AS avg_ms
FROM uptime_checks WHERE website_id = :id AND checked_at >= :s AND checked_at < :e GROUP BY 1 ORDER BY 1
""")
DAILY_ROLLUP_SQL = text("""
SELECT date_trunc('day', bucket_start) AS day, SUM(checks) AS checks, SUM(successes) AS ok,
       ROUND(SUM(avg_ms * successes) / NULLIF(SUM(successes) FILTER (WHERE avg_ms IS NOT NULL), 0)) AS avg_ms
FROM performance_metrics WHERE website_id = :id AND bucket_start >= :s AND bucket_start < :e GROUP BY 1 ORDER BY 1
""")
OVERALL_SQL = text("""
SELECT ROUND(AVG(response_ms) FILTER (WHERE ok)) AS avg_ms,
       ROUND(CAST(percentile_cont(0.95) WITHIN GROUP (ORDER BY response_ms) FILTER (WHERE ok) AS numeric)) AS p95_ms,
       COUNT(*) AS checks
FROM uptime_checks WHERE website_id = :id AND checked_at >= :s AND checked_at < :e
""")


def _response(db: Session, site_id, start, end) -> dict:
    p = {"id": site_id, "s": start, "e": end}
    daily = [dict(r._mapping) for r in db.execute(DAILY_SQL, p)]
    source = "checks"
    if not daily:
        daily = [dict(r._mapping) for r in db.execute(DAILY_ROLLUP_SQL, p)]
        source = "hourly rollups" if daily else "none"
    o = db.execute(OVERALL_SQL, p).one()
    avg = int(o.avg_ms) if o.avg_ms is not None else None
    if avg is None and daily:
        vals = [(d["avg_ms"], d["ok"]) for d in daily if d["avg_ms"] is not None and d["ok"]]
        avg = round(sum(float(a) * n for a, n in vals) / sum(n for _, n in vals)) if vals else None
    return {
        "avg_ms": avg, "p95_ms": int(o.p95_ms) if o.p95_ms is not None else None, "source": source,
        "daily": [{"day": d["day"].date().isoformat(), "checks": int(d["checks"]), "ok": int(d["ok"]),
                   "avg_ms": int(d["avg_ms"]) if d["avg_ms"] is not None else None} for d in daily],
    }


def _client_reason(reason: str | None) -> str | None:
    """Plain-language coverage reasons for client reports (no raw network/error details)."""
    if not reason:
        return reason
    if reason.startswith("The site could not be crawled") or "robots.txt request failed" in reason:
        if "robots.txt disallows" in reason:
            return "The website's robots.txt file does not allow automated crawling"
        return "The audit could not reach the website, so this check could not be performed"
    if reason.startswith("TLS connection could not be made"):
        return "The secure (HTTPS) connection could not be checked"
    return reason


def _audit(db: Session, site_id, start, end, technical: bool = True) -> dict | None:
    runs = list(db.scalars(select(AuditRun).where(
        AuditRun.website_id == site_id, AuditRun.status.in_(("completed", "partial")),
        AuditRun.finished_at >= start, AuditRun.finished_at < end).order_by(AuditRun.finished_at)))
    if not runs:
        return None
    last = runs[-1]
    s = last.summary or {}
    cov = last.check_coverage or {}
    by_status: dict[str, list] = {"performed": [], "partial": [], "not_checked": [], "not_applicable": []}
    for key, label in CHECKS.items():
        c = cov.get(key, {"status": "not_checked", "reason": "Not evaluated"})
        reason = c.get("reason") if technical else _client_reason(c.get("reason"))
        by_status.setdefault(c["status"], []).append({"label": label, "reason": reason})
    totals = {"new": 0, "recurring": 0, "resolved": 0}
    for r in runs:
        for k in totals:
            totals[k] += (r.summary or {}).get("counts", {}).get(k, 0)
    return {
        "runs": len(runs), "last_run_id": str(last.id), "last_finished_at": _iso(last.finished_at),
        "last_status": last.status, "pages_crawled": last.pages_crawled,
        "broken_internal_links": s.get("broken_internal_links", 0),
        "broken_external_links": s.get("broken_external_links", 0),
        "external_links_checked": s.get("external_links_checked", 0),
        "by_severity": {k: (s.get("by_severity") or {}).get(k, 0) for k in SEV},
        "could_not_check": (s.get("by_certainty") or {}).get("could_not_check", 0),
        "coverage": by_status, "period_counts": totals,
    }


def _issue_row(i: Issue, technical: bool) -> dict:
    row = {"title": i.title, "severity": i.severity, "category": CHECKS.get(i.check_key, i.check_key),
           "status": i.status, "first_seen": _iso(i.created_at), "resolved_at": _iso(i.resolved_at)}
    if technical:
        row.update({"url": i.url, "certainty": i.certainty, "occurrences": i.occurrences,
                    "reopened": i.reopened_count, "resolution_note": i.resolution_note})
    return row


def _sev_key(i: Issue):
    return (SEV.index(i.severity), i.created_at)


def build_snapshot(db: Session, site: Website, period: str, kind: str, now: datetime | None = None,
                   branding: dict | None = None) -> dict:
    now = now or datetime.now(UTC)
    technical = kind == "technical"
    start, end_full = period_bounds(period)
    end = min(end_full, now)
    pstart, pend = period_bounds(previous_period(period))
    client = db.get(Client, site.client_id)
    show_expiry = technical or bool(client and client.show_expiry_to_client)

    # ---- uptime -------------------------------------------------------------------------
    up = uptime_for_sites(db, [site.id], start, end, now=now)[site.id]
    up_prev = uptime_for_sites(db, [site.id], pstart, pend, now=now)[site.id]
    incidents = list(db.scalars(select(UptimeIncident).where(
        UptimeIncident.website_id == site.id, UptimeIncident.started_at < end,
        func.coalesce(UptimeIncident.ended_at, now) > start).order_by(UptimeIncident.started_at)))
    inc_rows = []
    for i in incidents:
        row = {"started_at": _iso(i.started_at), "ended_at": _iso(i.ended_at),
               "duration_s": i.duration_s if i.ended_at else int((now - i.started_at).total_seconds()),
               "ongoing": i.ended_at is None}
        if technical:
            row["cause"] = i.cause_summary
        inc_rows.append(row)

    resp = _response(db, site.id, start, end)
    resp_prev = _response(db, site.id, pstart, pend)
    audit = _audit(db, site.id, start, end, technical)
    audit_prev = _audit(db, site.id, pstart, pend, technical)

    # ---- issues -----------------------------------------------------------------------------
    open_q = select(Issue).where(Issue.website_id == site.id, Issue.status != "resolved")
    if not technical:
        open_q = open_q.where(Issue.severity != "info")
    outstanding = sorted(db.scalars(open_q), key=_sev_key)
    resolved = sorted(db.scalars(select(Issue).where(
        Issue.website_id == site.id, Issue.status == "resolved", Issue.resolved_at >= start,
        Issue.resolved_at < end)), key=_sev_key)
    new = sorted(db.scalars(select(Issue).where(
        Issue.website_id == site.id, Issue.created_at >= start, Issue.created_at < end)), key=_sev_key)
    if not technical:
        resolved = [i for i in resolved if i.severity != "info"]
        new = [i for i in new if i.severity != "info"]
    recurring_ids = []
    if audit:
        recurring_ids = [x for r in db.scalars(select(AuditRun).where(
            AuditRun.website_id == site.id, AuditRun.finished_at >= start, AuditRun.finished_at < end))
            for x in (r.summary or {}).get("recurring", [])]
    recurring = sorted(db.scalars(select(Issue).where(Issue.id.in_([uuid.UUID(x) for x in recurring_ids]))),
                       key=_sev_key) if recurring_ids else []
    limit = 60 if technical else 25

    # ---- expiry -----------------------------------------------------------------------------
    expiry_block = None
    if show_expiry:
        d = db.scalar(select(DomainRecord).where(DomainRecord.website_id == site.id))  # read-only
        deff = expiry.domain_effective(d)
        dname = (d.domain if d and (d.domain_overridden or registrable_domain(d.domain))
                 else registrable_domain(site.hostname))
        h = expiry.get_hosting_record(db, site)
        heff = expiry.hosting_effective(h)
        ssl_row = expiry.latest_ssl(db, site.id, completed_only=True)
        seff = expiry.ssl_effective(ssl_row)
        https = site.base_url.startswith("https://") and site.ssl_monitoring
        if not site.base_url.startswith("https://"):
            ssl_note = "Website uses http://"
        elif not site.ssl_monitoring:
            ssl_note = "SSL monitoring is turned off"
        elif technical or ssl_row is not None:
            ssl_note = seff.note  # e.g. "Certificate is not valid: ..." (the substantive problem)
        else:
            ssl_note = "The certificate could not be checked during this period"  # no raw connection errors
        expiry_block = {
            "domain": {"name": dname, "registrar": (d.registrar or d.manual_registrar) if d else None,
                       "date": _iso(deff.value), "days_left": deff.days_left(now.date()), "source": deff.source,
                       "as_of": _iso(deff.as_of), "note": deff.note},
            "hosting": {"provider": h.provider if h else None, "date": _iso(heff.value),
                        "days_left": heff.days_left(now.date()), "source": heff.source, "as_of": _iso(heff.as_of),
                        "note": heff.note},
            "ssl": {"applicable": https, "issuer": ssl_row.issuer if ssl_row else None,
                    "valid": ssl_row.valid if ssl_row else None, "date": _iso(seff.value),
                    "days_left": seff.days_left(now.date()), "as_of": _iso(seff.as_of),
                    "error": ssl_row.validation_error if ssl_row and ssl_row.valid is False else None,
                    "note": ssl_note},
        }

    # ---- maintenance performed ------------------------------------------------------------------
    mq = select(MaintenanceActivity).where(MaintenanceActivity.website_id == site.id,
                                           MaintenanceActivity.performed_at >= start, MaintenanceActivity.performed_at < end)
    if not technical:
        mq = mq.where(MaintenanceActivity.visible_to_client.is_(True))
    maintenance = [{"date": _iso(m.performed_at.date()), "title": m.title, "category": CATEGORY_LABELS.get(m.category, m.category),
                    "description": m.description} for m in db.scalars(mq.order_by(MaintenanceActivity.performed_at))]

    snap = {
        "version": 1,
        "kind": kind,
        "period": period,
        "period_start": _iso(start),
        "period_end": _iso(end_full),
        "data_until": _iso(end),
        "period_complete": end >= end_full,
        "generated_at": _iso(now),
        "website": {"name": site.name, "url": site.base_url, "client": client.name if client else ""},
        "branding": branding or {},
        "uptime": {**{k: _iso(v) for k, v in up.as_dict().items()}, "incidents": inc_rows,
                   "previous": {"uptime_pct": up_prev.uptime_pct, "downtime_s": up_prev.downtime_s,
                                "incidents": up_prev.incidents, "coverage_pct": up_prev.coverage_pct}},
        "response": {**resp, "previous_avg_ms": resp_prev["avg_ms"]},
        "audit": audit,
        "audit_previous": {"by_severity": audit_prev["by_severity"], "pages_crawled": audit_prev["pages_crawled"],
                           "broken_internal_links": audit_prev["broken_internal_links"]} if audit_prev else None,
        "issues": {
            "outstanding_total": len(outstanding),
            "outstanding_by_severity": {k: sum(1 for i in outstanding if i.severity == k) for k in SEV},
            "outstanding": [_issue_row(i, technical) for i in outstanding[:limit]],
            "resolved": [_issue_row(i, technical) for i in resolved[:limit]],
            "resolved_total": len(resolved),
            "new_total": len(new),
            "new": [_issue_row(i, technical) for i in new[:limit]],
            "recurring": [_issue_row(i, technical) for i in recurring[:limit]],
        },
        "expiry": expiry_block,
        "maintenance": maintenance,
    }
    snap["status"] = overall_status(snap)
    snap["summary"] = executive_summary(snap)
    snap["recommendations"] = recommendations(snap)
    return snap


# ---- derived text (always evidence-based) -------------------------------------------------------

def overall_status(s: dict) -> dict:
    reasons_crit, reasons_warn = [], []
    up = s["uptime"]
    if up["uptime_pct"] is not None:
        if up["uptime_pct"] < 99.0:
            reasons_crit.append(f"Uptime was {up['uptime_pct']:.2f}% (below 99%)")
        elif up["uptime_pct"] < 99.9:
            reasons_warn.append(f"Uptime was {up['uptime_pct']:.2f}% (below 99.9%)")
    if any(i["ongoing"] for i in up["incidents"]):
        reasons_crit.append("An outage was still ongoing when the report was generated")
    ob = s["issues"]["outstanding_by_severity"]
    if ob["critical"]:
        reasons_crit.append(f"{ob['critical']} critical issue(s) outstanding")
    if ob["high"]:
        reasons_warn.append(f"{ob['high']} high-severity issue(s) outstanding")
    e = s.get("expiry")
    if e:
        for label, part in (("Domain", e["domain"]), ("Hosting", e["hosting"])):
            dl = part["days_left"]
            if dl is not None and dl <= 14:
                reasons_crit.append(f"{label} {'expired' if dl < 0 else f'expires in {dl} days'}")
            elif dl is not None and dl <= 60:
                reasons_warn.append(f"{label} expires in {dl} days")
        if e["ssl"]["applicable"]:
            if e["ssl"]["valid"] is False:
                reasons_crit.append("SSL certificate is not valid")
            elif e["ssl"]["days_left"] is not None and e["ssl"]["days_left"] <= 14:
                reasons_crit.append(f"SSL certificate expires in {e['ssl']['days_left']} days")
            elif e["ssl"]["days_left"] is not None and e["ssl"]["days_left"] <= 30:
                reasons_warn.append(f"SSL certificate expires in {e['ssl']['days_left']} days")
    if reasons_crit:
        return {"level": "critical", "reasons": reasons_crit + reasons_warn}
    if reasons_warn:
        return {"level": "warning", "reasons": reasons_warn}
    if up["uptime_pct"] is None and s["audit"] is None:
        return {"level": "unknown", "reasons": ["No monitoring or audit data was recorded for this period"]}
    reasons = []
    if up["uptime_pct"] is not None:
        reasons.append(f"Uptime {up['uptime_pct']:.2f}%")
    else:
        reasons.append("No uptime data for this period")
    reasons.append("No critical or high-severity issues outstanding")
    return {"level": "healthy", "reasons": reasons}


def _fmt_dur(sec: int) -> str:
    h, r = divmod(int(sec), 3600)
    m = r // 60
    return f"{h} h {m} min" if h else f"{m} min"


def executive_summary(s: dict) -> list[str]:
    out = []
    up = s["uptime"]
    if up["uptime_pct"] is None:
        out.append("No uptime checks were recorded during this period, so availability cannot be reported.")
    else:
        line = f"The website was available {up['uptime_pct']:.2f}% of the monitored time"
        n = len(up["incidents"])
        partial = up["coverage_pct"] < 95
        if n:
            line += (f", with {n} outage{'s' if n != 1 else ''}; {_fmt_dur(up['downtime_s'])} of downtime was "
                     + ("observed while monitoring was running." if partial else "recorded."))
        else:
            line += " with no confirmed outages."
        if partial:
            line += f" Monitoring covered {up['coverage_pct']:.1f}% of the period."
        out.append(line)
        if any(i["ongoing"] for i in up["incidents"]):
            out.append("An outage was still ongoing when this report was generated.")
    r = s["response"]
    if r["avg_ms"] is not None:
        line = f"Average response time was {r['avg_ms']} ms"
        if r["previous_avg_ms"]:
            diff = r["avg_ms"] - r["previous_avg_ms"]
            line += f" ({'+' if diff >= 0 else '−'}{abs(diff)} ms compared with the previous month)"
        out.append(line + ".")
    a = s["audit"]
    if a is None:
        out.append("No technical audit was completed during this period.")
    elif a["pages_crawled"] == 0:
        out.append("The technical audit could not access the website, so no technical checks were performed "
                   "this period. The absence of reported issues does not mean there are none.")
    else:
        sev = a["by_severity"]
        out.append(f"The technical audit crawled {a['pages_crawled']} pages and found "
                   f"{a['broken_internal_links']} broken internal and {a['broken_external_links']} broken external links; "
                   f"{sev['critical'] + sev['high']} critical/high-severity issue(s) were present.")
    iss = s["issues"]
    if a is None or a["pages_crawled"] == 0:
        out.append(f"{iss['outstanding_total']} issue(s) from earlier audits remain outstanding; "
                   f"{iss['resolved_total']} were confirmed fixed this period.")
    else:
        out.append(f"{iss['resolved_total']} issue(s) were confirmed fixed by a follow-up audit this period; "
                   f"{iss['outstanding_total']} remain outstanding.")
    if s["maintenance"]:
        out.append(f"The team recorded {len(s['maintenance'])} maintenance activit{'ies' if len(s['maintenance']) != 1 else 'y'} "
                   "(listed separately from monitoring results).")
    return out


def recommendations(s: dict) -> list[dict]:
    recs = []

    def add(priority, text, evidence):
        recs.append({"priority": priority, "text": text, "evidence": evidence})

    ob = s["issues"]["outstanding_by_severity"]
    if ob["critical"] or ob["high"]:
        top = [i["title"] for i in s["issues"]["outstanding"] if i["severity"] in ("critical", "high")][:3]
        add("high", "Fix the outstanding critical and high-severity issues.",
            f"{ob['critical']} critical, {ob['high']} high: " + "; ".join(top))
    up = s["uptime"]
    if up["uptime_pct"] is not None and up["uptime_pct"] < 99.9:
        add("high" if up["uptime_pct"] < 99 else "medium", "Investigate the causes of downtime and harden hosting.",
            f"Uptime {up['uptime_pct']:.2f}% with {len(up['incidents'])} outage(s)")
    if up["uptime_pct"] is not None and up["coverage_pct"] < 90 and s["kind"] == "technical":
        add("medium", "Check the monitoring workers: part of the period was not monitored.",
            f"Coverage {up['coverage_pct']:.1f}%")
    r = s["response"]
    if r["avg_ms"] and r["previous_avg_ms"] and r["avg_ms"] > r["previous_avg_ms"] * 1.3 and r["avg_ms"] - r["previous_avg_ms"] > 200:
        add("medium", "Review performance: response times increased significantly.",
            f"{r['previous_avg_ms']} ms → {r['avg_ms']} ms")
    a = s["audit"]
    if a is None:
        add("medium", "Run a technical audit; none completed this period.", "No completed audit in the period")
    elif a["pages_crawled"] == 0:
        add("high", "Make the website reachable to the audit (or check its address); no technical checks could run.",
            "The audit crawled 0 pages")
    elif a["coverage"].get("not_checked"):
        names = ", ".join(c["label"] for c in a["coverage"]["not_checked"][:4])
        add("low", "Some audit checks could not be performed; review them manually.", names)
    e = s.get("expiry")
    if e:
        for label, part in (("domain", e["domain"]), ("hosting", e["hosting"])):
            dl = part["days_left"]
            if label == "domain" and not part.get("name"):
                continue  # no registrable domain (e.g. IP address): nothing to renew
            if part["date"] is None:
                add("low", f"Record the {label} renewal date so expiry can be tracked.",
                    part.get("note") or "No date available")
            elif dl is not None and dl <= 60:
                add("high" if dl <= 14 else "medium", f"Renew the {label} before it expires.",
                    f"{'Expired' if dl < 0 else f'{dl} days left'} ({part['date']}, source: {part['source']})")
        ssl = e["ssl"]
        if ssl["applicable"] and ssl["valid"] is False:
            add("high", "Replace the invalid SSL certificate.", ssl.get("error") or "Certificate not valid")
        elif ssl["applicable"] and ssl["days_left"] is not None and ssl["days_left"] <= 30:
            add("high" if ssl["days_left"] <= 14 else "medium", "Renew the SSL certificate (or confirm auto-renewal).",
                f"{ssl['days_left']} days left")
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(recs, key=lambda x: order[x["priority"]])
