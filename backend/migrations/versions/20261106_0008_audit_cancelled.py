"""Audit runs can be stopped (status 'cancelled')

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

OLD = "status IN ('queued', 'running', 'completed', 'partial', 'failed')"
NEW = "status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_audit_runs_audit_status"), "audit_runs", type_="check")
    op.create_check_constraint("audit_status", "audit_runs", NEW)


def downgrade() -> None:
    op.execute("UPDATE audit_runs SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint(op.f("ck_audit_runs_audit_status"), "audit_runs", type_="check")
    op.create_check_constraint("audit_status", "audit_runs", OLD)
