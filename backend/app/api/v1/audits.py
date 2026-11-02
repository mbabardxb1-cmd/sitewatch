"""Technical audits API (staff) and issue tracking API (staff + client summary)."""
import csv
import io
import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.checks import CHECKS
from app.audit.service import AuditBusy, create_manual_run
from app.auth.deps import client_ip, require
from app.auth.scope import get_website_or_404, not_found, website_scope_clause
from app.db import get_db
from app.models import AuditFinding, AuditRun, Issue, IssueComment, MonitoredUrl, User, Website
from app.netsafe.urls import UnsafeUrlError, normalize_site_url
from app.services.audit_log import record
from app.services.website_service import is_client_user

router = APIRouter(tags=["audits"])


class RunOut(BaseModel):
    id: uuid.UUID
    website_id: uuid.UUID
    website_name: str
    trigger: str
    period: str | None
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    pages_crawled: int
    summary: dict
    error: str | None


class RunDetail(RunOut):
    coverage: list[dict]
    config: dict


class FindingOut(BaseModel):
    id: uuid.UUID
    check_key: str
    check_label: str
    severity: str
    certainty: str
    title: str
    url: str | None
    source_url: str | None
    detail: dict


def _run_out(run: AuditRun, site: Website) -> dict:
    return dict(id=run.id, website_id=run.website_id, website_name=site.name, trigger=run.trigger, period=run.period,
                status=run.status, created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
                pages_crawled=run.pages_crawled, summary=run.summary or {}, error=run.error)


def _get_run(db: Session, user: User, run_id: uuid.UUID) -> tuple[AuditRun, Website]:
    row = db.execute(select(AuditRun, Website).join(Website, Website.id == AuditRun.website_id)
                     .where(AuditRun.id == run_id, website_scope_clause(user))).unique().first()
    if row is None:
        raise not_found()
    return row[0], row[1]


@router.get("/audits", response_model=list[RunOut])
def list_runs(website_id: uuid.UUID | None = None, limit: int = Query(default=50, ge=1, le=500),
              db: Session = Depends(get_db), user: User = Depends(require("audit.read_detailed"))):
    q = select(AuditRun, Website).join(Website, Website.id == AuditRun.website_id).where(website_scope_clause(user))
    if website_id:
        q = q.where(AuditRun.website_id == website_id)
    rows = db.execute(q.order_by(AuditRun.created_at.desc()).limit(limit)).unique().all()
    return [_run_out(r, w) for r, w in rows]


@router.post("/websites/{website_id}/audits", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def start_audit(website_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("audit.run"))):
    site = get_website_or_404(db, user, website_id)
    if site.status == "archived":
        raise HTTPException(status.HTTP_409_CONFLICT, "Archived websites cannot be audited.")
    try:
        run = create_manual_run(db, site, user.id)
    except AuditBusy:
        raise HTTPException(status.HTTP_409_CONFLICT, "An audit for this website is already queued or running.") from None
    record(db, user.id, "audit.run", "website", site.id, client_ip(request), run_id=str(run.id))
    db.commit()
    from app.worker.tasks import run_audit

    run_audit.delay(str(run.id))
    db.refresh(run)
    return _run_out(run, site)


@router.get("/audits/{run_id}", response_model=RunDetail)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("audit.read_detailed"))):
    run, site = _get_run(db, user, run_id)
    coverage = [{"check_key": k, "label": label, **(run.check_coverage or {}).get(k, {"status": "not_checked",
                 "reason": "Not evaluated in this run"})} for k, label in CHECKS.items()] if run.status in (
                     "completed", "partial") else []
    return RunDetail(**_run_out(run, site), coverage=coverage, config=run.config_snapshot or {})


@router.get("/audits/{run_id}/findings", response_model=list[FindingOut])
def run_findings(run_id: uuid.UUID, check_key: str | None = None, severity: str | None = None,
                 certainty: str | None = None, limit: int = Query(default=500, ge=1, le=5000),
                 offset: int = Query(default=0, ge=0), db: Session = Depends(get_db),
                 user: User = Depends(require("audit.read_detailed"))):
    run, _ = _get_run(db, user, run_id)
    q = select(AuditFinding).where(AuditFinding.audit_run_id == run.id)
    for col, val in ((AuditFinding.check_key, check_key), (AuditFinding.severity, severity),
                     (AuditFinding.certainty, certainty)):
        if val:
            q = q.where(col == val)
    sev_order = func.array_position(["critical", "high", "medium", "low", "info"], AuditFinding.severity)
    rows = db.scalars(q.order_by(sev_order, AuditFinding.check_key, AuditFinding.url).offset(offset).limit(limit))
    return [FindingOut(id=f.id, check_key=f.check_key, check_label=CHECKS.get(f.check_key, f.check_key),
                       severity=f.severity, certainty=f.certainty, title=f.title, url=f.url,
                       source_url=f.source_url, detail=f.detail) for f in rows]


@router.get("/audits/{run_id}/compare")
def compare_run(run_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("audit.read_detailed"))):
    run, site = _get_run(db, user, run_id)
    prev = db.scalar(select(AuditRun).where(AuditRun.website_id == run.website_id,
                                            AuditRun.status.in_(("completed", "partial")),
                                            AuditRun.created_at < run.created_at)
                     .order_by(AuditRun.created_at.desc()).limit(1))
    summary = run.summary or {}

    def issues(ids):
        if not ids:
            return []
        rows = db.scalars(select(Issue).where(Issue.id.in_([uuid.UUID(i) for i in ids])))
        return [{"id": str(i.id), "title": i.title, "severity": i.severity, "check_key": i.check_key,
                 "status": i.status, "resolution_note": i.resolution_note} for i in rows]

    cur_sev = summary.get("by_severity") or {}
    prev_sev = ((prev.summary or {}).get("by_severity") or {}) if prev else {}
    by_severity = {s: {"current": cur_sev.get(s, 0), "previous": prev_sev.get(s, 0) if prev else None}
                   for s in ("critical", "high", "medium", "low", "info")}

    return {
        "run_id": str(run.id),
        "previous_run_id": str(prev.id) if prev else None,
        "previous_finished_at": prev.finished_at if prev else None,
        "pages_crawled": {"current": run.pages_crawled, "previous": prev.pages_crawled if prev else None},
        "by_severity": by_severity,
        "new": issues(summary.get("new")),
        "recurring": issues(summary.get("recurring")),
        "resolved": issues(summary.get("resolved")),
        "persisting_count": len(summary.get("persisting") or []),
    }


@router.get("/audits/{run_id}/export")
def export_run(run_id: uuid.UUID, format: Literal["csv", "json"] = "csv", db: Session = Depends(get_db),
               user: User = Depends(require("audit.export"))):
    run, site = _get_run(db, user, run_id)
    findings = db.scalars(select(AuditFinding).where(AuditFinding.audit_run_id == run.id)
                          .order_by(AuditFinding.check_key, AuditFinding.url))
    stamp = (run.finished_at or run.created_at).strftime("%Y%m%d")
    name = f"audit-{site.hostname}-{stamp}"
    if format == "json":
        payload = {
            "website": {"name": site.name, "url": site.base_url},
            "run": {**{k: str(v) if v is not None else None for k, v in _run_out(run, site).items() if k != "summary"},
                    "summary": run.summary},
            "coverage": run.check_coverage,
            "findings": [{"check": f.check_key, "check_label": CHECKS.get(f.check_key), "severity": f.severity,
                          "certainty": f.certainty, "title": f.title, "url": f.url, "source_url": f.source_url,
                          "detail": f.detail} for f in findings],
        }
        return Response(json.dumps(payload, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["check", "severity", "certainty", "title", "url", "found_on", "detail"])
    for f in findings:
        # Neutralise spreadsheet formula injection from crawled content.
        row = [f.check_key, f.severity, f.certainty, f.title, f.url or "", f.source_url or "",
               json.dumps(f.detail, default=str)[:2000]]
        w.writerow(["'" + c if isinstance(c, str) and c[:1] in ("=", "+", "-", "@", "\t", "\r") else c for c in row])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# ---- Important URLs -----------------------------------------------------------------------

class ImportantUrlsIn(BaseModel):
    urls: list[str] = Field(max_length=200)


@router.get("/websites/{website_id}/important-urls", response_model=list[str])
def get_important(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("audit.read_detailed"))):
    site = get_website_or_404(db, user, website_id)
    return list(db.scalars(select(MonitoredUrl.url).where(MonitoredUrl.website_id == site.id,
                                                          MonitoredUrl.is_important.is_(True)).order_by(MonitoredUrl.url)))


@router.put("/websites/{website_id}/important-urls", response_model=list[str])
def set_important(website_id: uuid.UUID, body: ImportantUrlsIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("website.crawl_config"))):
    import hashlib

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.audit.crawler import site_key

    site = get_website_or_404(db, user, website_id)
    clean = []
    for raw in body.urls:
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("/"):
            raw = site.base_url.rstrip("/") + raw
        try:
            url, _ = normalize_site_url(raw)
        except UnsafeUrlError as e:
            raise HTTPException(422, f"{raw}: {e}") from None
        if site_key(url) != site_key(site.base_url):
            raise HTTPException(422, f"{raw}: important pages must be on the website itself.")
        clean.append(url)
    db.query(MonitoredUrl).filter(MonitoredUrl.website_id == site.id).update({"is_important": False})
    for url in dict.fromkeys(clean):
        db.execute(pg_insert(MonitoredUrl).values(
            website_id=site.id, url=url, url_hash=hashlib.sha256(url.encode()).hexdigest(), is_important=True,
        ).on_conflict_do_update(constraint="uq_monitored_urls_site_hash", set_={"is_important": True}))
    record(db, user.id, "website.important_urls", "website", site.id, client_ip(request), count=len(clean))
    db.commit()
    return get_important(website_id, db, user)


# ---- Issues --------------------------------------------------------------------------------

CLIENT_STATUS = {"open": "Open", "acknowledged": "Open", "in_progress": "Being worked on",
                 "resolved_pending_verification": "Fix awaiting verification", "resolved": "Resolved"}


class IssueClientView(BaseModel):
    id: uuid.UUID
    website_id: uuid.UUID
    website_name: str
    category: str
    title: str
    severity: str
    status: str  # simplified wording
    first_seen: datetime
    resolved_at: datetime | None


class IssueStaffView(BaseModel):
    id: uuid.UUID
    website_id: uuid.UUID
    website_name: str
    check_key: str
    category: str
    title: str
    severity: str
    certainty: str
    status: str
    url: str | None
    detail: dict
    occurrences: int
    reopened_count: int
    first_seen: datetime
    last_seen_run_id: uuid.UUID | None
    resolved_at: datetime | None
    resolved_verified_run_id: uuid.UUID | None
    resolution_note: str | None
    assignee_id: uuid.UUID | None
    assignee_email: str | None
    comment_count: int


def _issue_view(db: Session, i: Issue, site: Website, user: User):
    if is_client_user(user):
        return IssueClientView(id=i.id, website_id=i.website_id, website_name=site.name,
                               category=CHECKS.get(i.check_key, i.check_key), title=i.title, severity=i.severity,
                               status=CLIENT_STATUS[i.status], first_seen=i.created_at, resolved_at=i.resolved_at)
    assignee = db.get(User, i.assignee_id) if i.assignee_id else None
    n = db.scalar(select(func.count()).select_from(IssueComment).where(IssueComment.issue_id == i.id)) or 0
    return IssueStaffView(
        id=i.id, website_id=i.website_id, website_name=site.name, check_key=i.check_key,
        category=CHECKS.get(i.check_key, i.check_key), title=i.title, severity=i.severity, certainty=i.certainty,
        status=i.status, url=i.url, detail=i.detail or {}, occurrences=i.occurrences, reopened_count=i.reopened_count,
        first_seen=i.created_at, last_seen_run_id=i.last_seen_run_id, resolved_at=i.resolved_at,
        resolved_verified_run_id=i.resolved_verified_run_id, resolution_note=i.resolution_note,
        assignee_id=i.assignee_id, assignee_email=assignee.email if assignee else None, comment_count=n)


def _get_issue(db: Session, user: User, issue_id: uuid.UUID) -> tuple[Issue, Website]:
    row = db.execute(select(Issue, Website).join(Website, Website.id == Issue.website_id)
                     .where(Issue.id == issue_id, website_scope_clause(user))).unique().first()
    if row is None:
        raise not_found()
    return row[0], row[1]


@router.get("/issues", response_model=list[IssueStaffView | IssueClientView])
def list_issues(website_id: uuid.UUID | None = None, state: Literal["open", "resolved", "all"] = "open",
                severity: str | None = None, check_key: str | None = None,
                limit: int = Query(default=500, ge=1, le=2000), db: Session = Depends(get_db),
                user: User = Depends(require("issue.read_summary"))):
    q = select(Issue, Website).join(Website, Website.id == Issue.website_id).where(website_scope_clause(user))
    if state == "open":
        q = q.where(Issue.status != "resolved")
    elif state == "resolved":
        q = q.where(Issue.status == "resolved")
    if website_id:
        q = q.where(Issue.website_id == website_id)
    if severity:
        q = q.where(Issue.severity == severity)
    if check_key:
        q = q.where(Issue.check_key == check_key)
    if is_client_user(user):
        q = q.where(Issue.severity != "info")
    sev_order = func.array_position(["critical", "high", "medium", "low", "info"], Issue.severity)
    rows = db.execute(q.order_by(sev_order, Issue.created_at.desc()).limit(limit)).unique().all()
    return [_issue_view(db, i, w, user) for i, w in rows]


@router.get("/issues/{issue_id}", response_model=IssueStaffView | IssueClientView)
def get_issue(issue_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("issue.read_summary"))):
    i, w = _get_issue(db, user, issue_id)
    return _issue_view(db, i, w, user)


class IssueUpdate(BaseModel):
    # "resolved" is deliberately absent: only a verifying audit can resolve an issue.
    status: Literal["open", "acknowledged", "in_progress", "resolved_pending_verification"] | None = None
    assignee_id: uuid.UUID | None = None
    clear_assignee: bool = False


@router.patch("/issues/{issue_id}", response_model=IssueStaffView)
def update_issue(issue_id: uuid.UUID, body: IssueUpdate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("issue.manage"))):
    i, w = _get_issue(db, user, issue_id)
    if body.status:
        if i.status == "resolved":
            raise HTTPException(status.HTTP_409_CONFLICT, "Resolved issues re-open automatically if an audit finds them again.")
        i.status = body.status
    if body.clear_assignee:
        i.assignee_id = None
    elif body.assignee_id:
        assignee = db.get(User, body.assignee_id)
        if assignee is None or is_client_user(assignee) or not assignee.is_active:
            raise HTTPException(422, "Assignee must be an active staff user.")
        if db.scalar(select(Website.id).where(Website.id == w.id, website_scope_clause(assignee))) is None:
            raise HTTPException(422, "Assignee does not have access to this website.")
        i.assignee_id = assignee.id
    record(db, user.id, "issue.update", "issue", i.id, client_ip(request),
           **body.model_dump(mode="json", exclude_none=True))
    db.commit()
    return _issue_view(db, i, w, user)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class CommentOut(BaseModel):
    id: uuid.UUID
    author_email: str | None
    body: str
    created_at: datetime


@router.get("/issues/{issue_id}/comments", response_model=list[CommentOut])
def list_comments(issue_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("issue.note.read"))):
    i, _ = _get_issue(db, user, issue_id)
    rows = db.execute(select(IssueComment, User.email).outerjoin(User, User.id == IssueComment.author_id)
                      .where(IssueComment.issue_id == i.id).order_by(IssueComment.created_at)).all()
    return [CommentOut(id=c.id, author_email=e, body=c.body, created_at=c.created_at) for c, e in rows]


@router.post("/issues/{issue_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(issue_id: uuid.UUID, body: CommentIn, db: Session = Depends(get_db),
                user: User = Depends(require("issue.note.write"))):
    i, _ = _get_issue(db, user, issue_id)
    c = IssueComment(issue_id=i.id, author_id=user.id, body=body.body.strip())
    db.add(c)
    db.commit()
    return CommentOut(id=c.id, author_email=user.email, body=c.body, created_at=c.created_at)
