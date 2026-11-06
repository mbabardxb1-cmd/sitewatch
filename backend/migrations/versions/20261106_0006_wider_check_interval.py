"""Allow check intervals from 1 minute to 24 hours

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_websites_check_interval_range"), "websites", type_="check")
    op.create_check_constraint("check_interval_range", "websites", "check_interval_s BETWEEN 60 AND 86400")


def downgrade() -> None:
    # Clamp longer intervals back into the old 1-5 minute range before restoring the constraint.
    op.execute("UPDATE websites SET check_interval_s = 300 WHERE check_interval_s > 300")
    op.drop_constraint(op.f("ck_websites_check_interval_range"), "websites", type_="check")
    op.create_check_constraint("check_interval_range", "websites", "check_interval_s BETWEEN 60 AND 300")
