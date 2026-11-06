"""Audit categories/progress, optional automatic audits, audit.delete permission

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("websites", sa.Column("auto_audit", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("audit_runs", sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()),
                                          server_default=sa.text("'{}'::jsonb"), nullable=False))

    from sqlalchemy.orm import Session

    from app.auth.permissions import sync_roles_and_permissions

    session = Session(bind=op.get_bind())
    sync_roles_and_permissions(session)
    session.flush()


def downgrade() -> None:
    op.drop_column("audit_runs", "progress")
    op.drop_column("websites", "auto_audit")
