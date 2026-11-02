import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.auth.ratelimit import RateLimiter, get_redis
from app.auth.scope import visible_website_ids
from app.db import get_db
from app.models import Client, NotificationLog, NotificationRule, User, Website
from app.models.ops import NOTIFICATION_EVENTS
from app.notify.senders import NotifierError, get_notifier
from app.notify.service import EVENT_LABELS
from app.services.audit_log import record

router = APIRouter(tags=["notifications"])


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str
    website_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    enabled: bool = True
    recipients: list[EmailStr] = Field(default_factory=list, max_length=50)
    thresholds: dict = Field(default_factory=dict)
    min_interval_s: int = Field(default=0, ge=0, le=7 * 86400)


class RuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    recipients: list[EmailStr] | None = Field(default=None, max_length=50)
    thresholds: dict | None = None
    min_interval_s: int | None = Field(default=None, ge=0, le=7 * 86400)


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_type: str
    website_id: uuid.UUID | None
    client_id: uuid.UUID | None
    enabled: bool
    recipients: list[str]
    thresholds: dict
    min_interval_s: int
    created_at: datetime


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    website_id: uuid.UUID | None
    event_type: str
    channel: str
    recipients: list[str]
    subject: str | None
    status: str
    error: str | None
    created_at: datetime
    sent_at: datetime | None


@router.get("/notification-events")
def events(_: User = Depends(require("notification.read"))):
    return [{"key": k, "label": EVENT_LABELS.get(k, k)} for k in NOTIFICATION_EVENTS]


def _validate_scope(db: Session, body: RuleIn) -> None:
    if body.event_type not in NOTIFICATION_EVENTS:
        raise HTTPException(422, "Unknown event type.")
    if body.website_id and body.client_id:
        raise HTTPException(422, "A rule applies to a website OR a client OR everything, not both.")
    if body.website_id and db.get(Website, body.website_id) is None:
        raise HTTPException(422, "Website does not exist.")
    if body.client_id and db.get(Client, body.client_id) is None:
        raise HTTPException(422, "Client does not exist.")


@router.get("/notification-rules", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db), _: User = Depends(require("notification.manage"))):
    return list(db.scalars(select(NotificationRule).order_by(NotificationRule.created_at)))


@router.post("/notification-rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("notification.manage"))):
    _validate_scope(db, body)
    rule = NotificationRule(**body.model_dump(mode="json"))
    db.add(rule)
    db.flush()
    record(db, user.id, "notification_rule.create", "notification_rule", rule.id, client_ip(request))
    db.commit()
    return rule


def _rule(db: Session, rule_id: uuid.UUID) -> NotificationRule:
    rule = db.get(NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return rule


@router.patch("/notification-rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, body: RuleUpdate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("notification.manage"))):
    rule = _rule(db, rule_id)
    for k, v in body.model_dump(exclude_unset=True, mode="json").items():
        if v is not None:
            setattr(rule, k, v)
    record(db, user.id, "notification_rule.update", "notification_rule", rule.id, client_ip(request))
    db.commit()
    return rule


@router.delete("/notification-rules/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("notification.manage"))):
    rule = _rule(db, rule_id)
    db.delete(rule)
    record(db, user.id, "notification_rule.delete", "notification_rule", rule_id, client_ip(request))
    db.commit()


@router.get("/notification-logs", response_model=list[LogOut])
def list_logs(website_id: uuid.UUID | None = None, limit: int = Query(default=100, ge=1, le=500),
              db: Session = Depends(get_db), user: User = Depends(require("notification.read"))):
    q = select(NotificationLog)
    if "website.all" not in user.permission_keys:
        q = q.where(NotificationLog.website_id.in_(visible_website_ids(db, user)))
    if website_id:
        q = q.where(NotificationLog.website_id == website_id)
    return list(db.scalars(q.order_by(NotificationLog.id.desc()).limit(limit)))


class TestEmailIn(BaseModel):
    recipient: EmailStr


@router.post("/notifications/test")
def send_test(body: TestEmailIn, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require("notification.manage"))):
    if not RateLimiter(get_redis()).hit(f"testmail:{user.id}", 5, 300):
        raise HTTPException(429, "Too many test emails; try again in a few minutes.")
    record(db, user.id, "notification.test", None, None, client_ip(request), recipient=body.recipient)
    db.commit()
    try:
        get_notifier("email").send([body.recipient], "[SiteWatch] Test notification",
                                   "This is a test message from SiteWatch. Email delivery is working.")
    except NotifierError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "error": None}
