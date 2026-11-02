"""Tenant isolation: which websites may a user see?

Every query touching website-owned data MUST be filtered through `website_scope_clause`
or `get_website_or_404`. Out-of-scope resources return 404 (not 403) so IDs cannot be
probed for existence.
"""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, select, true
from sqlalchemy.orm import Session

from app.auth.permissions import CLIENT
from app.models import User, Website, website_user_assignments


def website_scope_clause(user: User) -> ColumnElement[bool]:
    if "website.all" in user.permission_keys:
        return true()
    assigned = select(website_user_assignments.c.website_id).where(
        website_user_assignments.c.user_id == user.id
    )
    clause = Website.id.in_(assigned)
    if CLIENT in user.role_keys:
        # Defence in depth: a client user is additionally pinned to their own organization,
        # so a mistaken assignment to another client's website still grants nothing.
        if user.client_id is None:
            return false()
        clause = and_(clause, Website.client_id == user.client_id)
    return clause


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def get_website_or_404(db: Session, user: User, website_id: uuid.UUID) -> Website:
    site = db.scalar(select(Website).where(Website.id == website_id, website_scope_clause(user)))
    if site is None:
        raise not_found()
    return site


def visible_website_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(db.scalars(select(Website.id).where(website_scope_clause(user))))
