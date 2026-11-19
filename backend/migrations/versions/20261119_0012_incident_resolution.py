"""Manual incident resolution and the administrator's choice to include incidents in reports

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uptime_incidents", sa.Column("resolution", sa.String(length=20), nullable=True))
    op.add_column("uptime_incidents", sa.Column("resolved_by", sa.Uuid(), nullable=True))
    op.add_column("uptime_incidents", sa.Column("resolution_note", sa.Text(), nullable=True))
    op.add_column("uptime_incidents", sa.Column("include_in_report", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("uptime_incidents", sa.Column("excluded_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_uptime_incidents_resolved_by_users"), "uptime_incidents", "users",
                          ["resolved_by"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_uptime_incidents_excluded_by_users"), "uptime_incidents", "users",
                          ["excluded_by"], ["id"], ondelete="SET NULL")
    op.execute("UPDATE uptime_incidents SET resolution = 'auto' WHERE ended_at IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_uptime_incidents_excluded_by_users"), "uptime_incidents", type_="foreignkey")
    op.drop_constraint(op.f("fk_uptime_incidents_resolved_by_users"), "uptime_incidents", type_="foreignkey")
    for c in ("excluded_by", "include_in_report", "resolution_note", "resolved_by", "resolution"):
        op.drop_column("uptime_incidents", c)
