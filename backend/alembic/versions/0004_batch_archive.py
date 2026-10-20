"""Batch archive: a third batch status plus who/when.

Expand only. Widening the status check and adding nullable columns keeps the previous release
working: it never writes 'archived' and ignores the new columns.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("batches", sa.Column("archived_by", sa.String(128), nullable=True))
    op.drop_constraint("ck_batches_status", "batches", type_="check")
    op.create_check_constraint(
        "ck_batches_status", "batches", "status IN ('open', 'closed', 'archived')"
    )


def downgrade() -> None:
    # Archived batches become closed: still not claimable, nothing is lost.
    op.execute("UPDATE batches SET status = 'closed' WHERE status = 'archived'")
    op.drop_constraint("ck_batches_status", "batches", type_="check")
    op.create_check_constraint("ck_batches_status", "batches", "status IN ('open', 'closed')")
    op.drop_column("batches", "archived_by")
    op.drop_column("batches", "archived_at")
