"""User administration: an immediate off-switch and token invalidation after role changes.

Expand only (a NOT NULL column with a default, and a nullable column), so the previous release
keeps working.

active              false = the API refuses the user even while their ID token is still valid.
tokens_valid_after  set when roles change or the user is disabled; older ID tokens get a 401
                    so the client refreshes and picks up the new groups.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "users", sa.Column("tokens_valid_after", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "tokens_valid_after")
    op.drop_column("users", "active")
