"""Monthly reports, maintenance log and report branding."""
import io
import re
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require, require_any
from app.auth.scope import get_website_or_404, not_found, website_scope_clause
from app.db import get_db
from app.models import MaintenanceActivity, MonthlyReport, User, Website
from app.reports import service as rsvc
from app.services.audit_log import record
from app.services.website_service import is_client_user

router = APIRouter(tags=["reports"])
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ReportOut(BaseModel):
    id: uuid.UUID
    website_id: uuid.UUID
    website_name: str
    period: str
    kind: str
    status: str
    overall_level: str | None
    generated_at: datetime | None
    size_bytes: int | None
    approved_for_client: bool
    approved_at: datetime | None = None
    emailed_at: datetime | None = None
    emailed_to: list[str] = []
    error: str | None = None
    generation_count: int = 0


def _out(r: MonthlyReport, site: Website, user: User) -> ReportOut:
    base = dict(id=r.id, website_id=r.website_id, website_name=site.name, period=r.period, kind=r.kind, status=r.status,
                overall_level=((r.data_snapshot or {}).get("status") or {}).get("level"), generated_at=r.generated_at,
                size_bytes=r.size_bytes, approved_for_client=r.approved_for_client)
    if is_client_user(user):
        return ReportOut(**base)
    return ReportOut(**base, approved_at=r.approved_at, emailed_at=r.emailed_at, emailed_to=r.emailed_to or [],
                     error=r.error, generation_count=r.generation_count)


def _client_visible(q):
    return q.where(MonthlyReport.kind == "client", MonthlyReport.approved_for_client.is_(True),
                   MonthlyReport.status == "ready")


def _get(db: Session, user: User, report_id: uuid.UUID) -> tuple[MonthlyReport, Website]:
    q = (select(MonthlyReport, Website).join(Website, Website.id == MonthlyReport.website_id)
         .where(MonthlyReport.id == report_id, website_scope_clause(user)))
    if "report.read_all" not in user.permission_keys:
        q = _client_visible(q)
    row = db.execute(q).unique().first()
    if row is None:
        raise not_found()
    return row[0], row[1]


@router.get("/reports", response_model=list[ReportOut])
def list_reports(website_id: uuid.UUID | None = None, period: str | None = None,
                 kind: str | None = Query(default=None, pattern="^(client|technical)$"),
                 limit: int = Query(default=200, ge=1, le=1000),
                 db: Session = Depends(get_db), user: User = Depends(require("report.read"))):
    q = select(MonthlyReport, Website).join(Website, Website.id == MonthlyReport.website_id).where(website_scope_clause(user))
    if "report.read_all" not in user.permission_keys:
        q = _client_visible(q)
    if website_id:
        q = q.where(MonthlyReport.website_id == website_id)
    if period:
        q = q.where(MonthlyReport.period == period)
    if kind:
        q = q.where(MonthlyReport.kind == kind)
    rows = db.execute(q.order_by(MonthlyReport.period.desc(), Website.name, MonthlyReport.kind).limit(limit)).unique().all()
    return [_out(r, w, user) for r, w in rows]


class GenerateIn(BaseModel):
    period: str = Field(description="YYYY-MM")
    kind: str = Field(default="both", pattern="^(client|technical|both)$")
    regenerate: bool = False


@router.post("/websites/{website_id}/reports", response_model=list[ReportOut], status_code=status.HTTP_202_ACCEPTED)
def generate_reports(website_id: uuid.UUID, body: GenerateIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("report.generate"))):
    site = get_website_or_404(db, user, website_id)
    if not PERIOD_RE.match(body.period):
        raise HTTPException(422, "Period must be YYYY-MM.")
    if body.period > datetime.now(UTC).strftime("%Y-%m"):
        raise HTTPException(422, "Cannot report on a future month.")
    try:
        rows = rsvc.request_generation(db, site, body.period, body.kind, user.id, body.regenerate)
    except rsvc.ReportError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    record(db, user.id, "report.generate", "website", site.id, client_ip(request), period=body.period, kind=body.kind)
    db.commit()
    from app.worker.tasks import generate_report

    for r in rows:
        generate_report.delay(str(r.id))
    for r in rows:
        db.refresh(r)
    return [_out(r, site, user) for r in rows]


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("report.read"))):
    r, site = _get(db, user, report_id)
    return _out(r, site, user)


@router.get("/reports/{report_id}/download")
def download(report_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require("report.read"))):
    r, site = _get(db, user, report_id)
    try:
        data = rsvc.read_file(r)
    except (rsvc.ReportError, FileNotFoundError):
        raise HTTPException(status.HTTP_409_CONFLICT, "The report file is not available.") from None
    record(db, user.id, "report.download", "report", r.id, client_ip(request))
    db.commit()
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{rsvc.download_name(r, site)}"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class ApproveIn(BaseModel):
    email: bool = False


@router.post("/reports/{report_id}/approve", response_model=ReportOut)
def approve(report_id: uuid.UUID, body: ApproveIn, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require("report.approve"))):
    r, site = _get(db, user, report_id)
    try:
        log_id = rsvc.approve(db, r, user.id, body.email)
    except rsvc.ReportError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    record(db, user.id, "report.approve", "report", r.id, client_ip(request), email=body.email)
    db.commit()
    if log_id:
        from app.worker.tasks import send_notification

        send_notification.delay(log_id)
    db.refresh(r)
    return _out(r, site, user)


@router.post("/reports/{report_id}/withdraw", response_model=ReportOut)
def withdraw(report_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require("report.approve"))):
    r, site = _get(db, user, report_id)
    r.approved_for_client, r.approved_by, r.approved_at = False, None, None
    record(db, user.id, "report.withdraw", "report", r.id, client_ip(request))
    db.commit()
    return _out(r, site, user)


# ---- Maintenance log ---------------------------------------------------------------------

class MaintenanceIn(BaseModel):
    performed_at: date
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    category: str = Field(default="other", pattern="^(update|fix|security|content|performance|backup|other)$")
    visible_to_client: bool = True
    issue_id: uuid.UUID | None = None


class MaintenanceOut(MaintenanceIn):
    id: uuid.UUID
    performed_by_email: str | None


def _m_out(db: Session, m: MaintenanceActivity) -> MaintenanceOut:
    u = db.get(User, m.performed_by) if m.performed_by else None
    return MaintenanceOut(id=m.id, performed_at=m.performed_at.date(), title=m.title, description=m.description,
                          category=m.category, visible_to_client=m.visible_to_client, issue_id=m.issue_id,
                          performed_by_email=u.email if u else None)


@router.get("/websites/{website_id}/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(website_id: uuid.UUID, db: Session = Depends(get_db),
                     user: User = Depends(require_any("maintenance.write", "report.read_all"))):
    site = get_website_or_404(db, user, website_id)
    rows = db.scalars(select(MaintenanceActivity).where(MaintenanceActivity.website_id == site.id)
                      .order_by(MaintenanceActivity.performed_at.desc()).limit(200))
    return [_m_out(db, m) for m in rows]


@router.post("/websites/{website_id}/maintenance", response_model=MaintenanceOut, status_code=201)
def add_maintenance(website_id: uuid.UUID, body: MaintenanceIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("maintenance.write"))):
    site = get_website_or_404(db, user, website_id)
    if body.performed_at > datetime.now(UTC).date():
        raise HTTPException(422, "Maintenance can only be recorded once it has been performed (no future dates).")
    if body.issue_id:
        from app.models import Issue

        issue = db.get(Issue, body.issue_id)
        if issue is None or issue.website_id != site.id:
            raise HTTPException(422, "Issue not found for this website.")
    m = MaintenanceActivity(website_id=site.id, performed_by=user.id,
                            performed_at=datetime.combine(body.performed_at, datetime.min.time(), UTC),
                            **body.model_dump(exclude={"performed_at"}))
    db.add(m)
    db.flush()
    record(db, user.id, "maintenance.add", "website", site.id, client_ip(request), activity=str(m.id))
    db.commit()
    return _m_out(db, m)


@router.delete("/websites/{website_id}/maintenance/{activity_id}", status_code=204)
def delete_maintenance(website_id: uuid.UUID, activity_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("maintenance.write"))):
    site = get_website_or_404(db, user, website_id)
    m = db.get(MaintenanceActivity, activity_id)
    if m is None or m.website_id != site.id:
        raise not_found()
    db.delete(m)
    record(db, user.id, "maintenance.delete", "website", site.id, client_ip(request), activity=str(activity_id))
    db.commit()


# ---- Branding (report template) ------------------------------------------------------------

class BrandingIn(BaseModel):
    company_name: str | None = Field(default=None, max_length=120)
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    footer_text: str | None = Field(default=None, max_length=200)


@router.get("/settings/branding")
def get_branding(db: Session = Depends(get_db), _: User = Depends(require("settings.manage"))):
    b = rsvc.branding(db)
    return {**{k: b.get(k) for k in ("company_name", "accent_color", "footer_text")}, "has_logo": bool(rsvc.logo_file(db))}


@router.put("/settings/branding")
def put_branding(body: BrandingIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("settings.manage"))):
    for k, v in body.model_dump(exclude_unset=True).items():
        rsvc.set_setting(db, f"branding.{k}", v.strip() if isinstance(v, str) else v, user.id)
    record(db, user.id, "settings.branding", None, None, client_ip(request))
    db.commit()
    return get_branding(db, user)


MAX_LOGO_BYTES = 1024 * 1024


@router.post("/settings/branding/logo")
async def upload_logo(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                      user: User = Depends(require("settings.manage"))):
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(413, "Logo must be 1 MB or smaller.")
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()  # structural check
        if fmt not in ("PNG", "JPEG"):
            raise HTTPException(415, "Logo must be a PNG or JPEG image.")
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > 25_000_000:
                raise HTTPException(413, "Image dimensions are too large.")
            img = img.convert("RGBA")
            img.thumbnail((1200, 400))
            out = io.BytesIO()
            img.save(out, format="PNG")  # re-encode: strips metadata and anything that isn't pixels
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(415, "File is not a valid PNG or JPEG image.") from None
    rel = "branding/logo.png"
    path = rsvc.safe_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.getvalue())
    rsvc.set_setting(db, "branding.logo_path", rel, user.id)
    record(db, user.id, "settings.logo", None, None, client_ip(request), size=len(data))
    db.commit()
    return {"has_logo": True, "width": img.width, "height": img.height}


@router.delete("/settings/branding/logo", status_code=204)
def delete_logo(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings.manage"))):
    rsvc.set_setting(db, "branding.logo_path", None, user.id)
    record(db, user.id, "settings.logo_delete", None, None, client_ip(request))
    db.commit()
