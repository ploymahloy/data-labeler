"""The migration safety checker: it must flag what breaks a rolling deploy and pass what doesn't."""

import pytest

from scripts.check_migrations import APPROVAL_MARKER, check_source, check_versions, roundtrip
from tests.conftest import TEST_DATABASE_URL


def _migration(body: str, header: str = "") -> str:
    return f"{header}\nfrom alembic import op\nimport sqlalchemy as sa\n\ndef upgrade():\n{body}\n\ndef downgrade():\n    pass\n"


@pytest.mark.parametrize(
    "body",
    [
        "    op.drop_column('images', 'sha256')",
        "    op.drop_table('reviews')",
        "    op.rename_table('images', 'pictures')",
        "    op.alter_column('images', 'sha256', type_=sa.Text())",
        "    op.alter_column('images', 'sha256', new_column_name='digest')",
        "    op.alter_column('images', 'sha256', nullable=False)",
        "    op.add_column('images', sa.Column('flag', sa.Boolean(), nullable=False))",
        "    op.execute('ALTER TABLE images DROP COLUMN sha256')",
        "    op.execute('ALTER TABLE images RENAME COLUMN a TO b')",
        "    op.execute('ALTER TABLE images ALTER COLUMN a SET NOT NULL')",
        "    op.execute('ALTER TABLE images ALTER COLUMN a TYPE text')",
        "    op.execute('DROP TABLE images')",
    ],
)
def test_destructive_operations_are_flagged(body: str):
    assert check_source(_migration(body)), body


@pytest.mark.parametrize(
    "body",
    [
        "    op.add_column('images', sa.Column('note', sa.Text(), nullable=True))",
        "    op.add_column('users', sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))",
        "    op.create_index('ix', 'images', ['id'])",
        "    op.execute('CREATE INDEX ix ON images (id)')",
        "    op.create_table('t', sa.Column('id', sa.Integer(), primary_key=True))",
        "    op.alter_column('images', 'sha256', nullable=True)",
        "    op.drop_index('ix')",
    ],
)
def test_additive_operations_pass(body: str):
    assert check_source(_migration(body)) == []


def test_destructive_downgrade_is_fine():
    source = (
        "from alembic import op\n\ndef upgrade():\n    op.execute('CREATE INDEX ix ON t (a)')\n\n"
        "def downgrade():\n    op.drop_column('t', 'a')\n    op.drop_table('t')\n"
    )
    assert check_source(source) == []


def test_approval_marker_allows_contract_step():
    body = "    op.drop_column('images', 'legacy')"
    header = f"{APPROVAL_MARKER}: release 41 stopped reading images.legacy"
    assert check_source(_migration(body, header)) == []


def test_findings_name_file_and_line():
    (finding,) = check_source(_migration("    op.drop_table('reviews')"), "0099_x.py")
    assert finding.file == "0099_x.py"
    assert finding.line > 1
    assert "drop_table" in str(finding)


def test_shipped_migrations_are_safe():
    assert check_versions() == []


async def test_upgrade_downgrade_upgrade_round_trip_is_clean():
    server_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    assert await roundtrip(server_url) == []
