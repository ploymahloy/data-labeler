"""Partial indexes so queue-health queries (reaper and /metrics) stay cheap at any table size.

Additive only: safe to apply while the previous release is still serving traffic.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_images_submitted ON images (id) WHERE status = 'submitted'")
    op.execute(
        "CREATE INDEX ix_images_lease_expiry ON images (lease_expires_at) "
        "WHERE assigned_to IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_images_lease_expiry")
    op.execute("DROP INDEX IF EXISTS ix_images_submitted")
