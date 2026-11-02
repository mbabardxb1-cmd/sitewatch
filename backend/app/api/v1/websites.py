"""Website registry API. Every lookup goes through the tenant scope (404 when out of scope)."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.auth.scope import get_website_or_404, website_scope_clause
from app.db import get_db
from app.models import User, Website
from app.schemas.websites import (
    AssignmentsUpdate,
    CrawlConfig,
    WebsiteClientView,
    WebsiteCreate,
    WebsiteStaffView,
    WebsiteUpdate,
)
from app.services import website_service as svc
from app.services.audit_log import record

router = APIRouter(prefix="/websites", tags=["websites"])
WebsiteView = WebsiteStaffView | WebsiteClientView


@router.get("", response_model=list[WebsiteView])
def list_websites(
    db: Session = Depends(get_db),
    user: User = Depends(require("website.read")),
    status: Literal["active", "paused", "archived", "all"] = "all",
    client_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
):
    stmt = select(Website).where(website_scope_clause(user)).order_by(Website.name)
    if status != "all":
        stmt = stmt.where(Website.status == status)
    elif svc.is_client_user(user):
        stmt = stmt.where(Website.status != "archived")
    if client_id:
        stmt = stmt.where(Website.client_id == client_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Website.name.ilike(like), Website.base_url.ilike(like)))
    return [svc.to_view(s, user) for s in db.scalars(stmt).unique()]


@router.post("", response_model=WebsiteStaffView, status_code=201)
def create_website(body: WebsiteCreate, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("website.write"))):
    site = svc.create_website(db, user, body)
    record(db, user.id, "website.create", "website", site.id, client_ip(request), url=site.base_url)
    db.commit()
    return svc.to_view(site, user)


@router.get("/{website_id}", response_model=WebsiteView)
def get_website(website_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("website.read"))):
    return svc.to_view(get_website_or_404(db, user, website_id), user)


@router.patch("/{website_id}", response_model=WebsiteStaffView)
def update_website(website_id: uuid.UUID, body: WebsiteUpdate, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("website.write"))):
    site = get_website_or_404(db, user, website_id)
    fields = svc.update_website(db, site, body)
    record(db, user.id, "website.update", "website", site.id, client_ip(request), fields=fields)
    db.commit()
    db.refresh(site)
    return svc.to_view(site, user)


@router.put("/{website_id}/crawl-config", response_model=WebsiteStaffView)
def update_crawl_config(website_id: uuid.UUID, body: CrawlConfig, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("website.crawl_config"))):
    site = get_website_or_404(db, user, website_id)
    site.crawl_config = body.model_dump()
    record(db, user.id, "website.crawl_config", "website", site.id, client_ip(request))
    db.commit()
    db.refresh(site)
    return svc.to_view(site, user)


@router.post("/{website_id}/archive", response_model=WebsiteStaffView)
def archive_website(website_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("website.write"))):
    site = get_website_or_404(db, user, website_id)
    svc.archive(site)
    record(db, user.id, "website.archive", "website", site.id, client_ip(request))
    db.commit()
    return svc.to_view(site, user)


@router.post("/{website_id}/restore", response_model=WebsiteStaffView)
def restore_website(website_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("website.write"))):
    site = get_website_or_404(db, user, website_id)
    svc.restore(db, site)
    record(db, user.id, "website.restore", "website", site.id, client_ip(request))
    db.commit()
    return svc.to_view(site, user)


@router.put("/{website_id}/assignments", response_model=WebsiteStaffView)
def set_assignments(website_id: uuid.UUID, body: AssignmentsUpdate, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("website.assign"))):
    site = get_website_or_404(db, user, website_id)
    svc.set_assignments(db, site, body.user_ids)
    record(db, user.id, "website.assign", "website", site.id, client_ip(request),
           user_ids=[str(u) for u in body.user_ids])
    db.commit()
    db.refresh(site)
    return svc.to_view(site, user)
