"""Index for keyset-paginated export: (batch_id, created_at, id).

Additive only. Built CONCURRENTLY so a large images table is not write-locked while it builds.
That cannot run inside a transaction, hence the autocommit block.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_images_batch_created "
            "ON images (batch_id, created_at, id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_images_batch_created")
