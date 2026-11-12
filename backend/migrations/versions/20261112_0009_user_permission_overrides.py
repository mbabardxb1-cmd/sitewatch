"""Per-user permission overrides

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_permission_overrides",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], name=op.f("fk_user_permission_overrides_permission_id_permissions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_permission_overrides_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "permission_id", name=op.f("pk_user_permission_overrides")),
    )


def downgrade() -> None:
    op.drop_table("user_permission_overrides")
