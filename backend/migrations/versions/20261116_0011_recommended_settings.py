"""Apply the shared-hosting friendly monitoring settings to all existing websites (one time)

Uptime every 4 hours (or less often if already set), 30 s timeouts, degraded above 5 s, crawls at
1 request/second and at most 200 pages, no daily malware scan. Never makes checks heavier.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain SQL (not the ORM) so this migration keeps working when models change later.
    op.execute("""
        UPDATE websites SET
            check_interval_s = GREATEST(check_interval_s, 14400),
            timeout_s = 30,
            degraded_threshold_ms = GREATEST(degraded_threshold_ms, 5000),
            retry_attempts = LEAST(retry_attempts, 2),
            recovery_threshold = 1,
            malware_auto_scan = false,
            crawl_config = crawl_config || jsonb_build_object(
                'rate_rps', LEAST(COALESCE((crawl_config->>'rate_rps')::float, 1.0), 1.0),
                'max_pages', LEAST(COALESCE((crawl_config->>'max_pages')::int, 200), 200),
                'max_depth', LEAST(COALESCE((crawl_config->>'max_depth')::int, 3), 3),
                'timeout_s', 30,
                'render_js', false)
        WHERE status <> 'archived'
    """)


def downgrade() -> None:
    pass  # settings changes are not reverted
