"""Migration safety checks, run in CI.

    python -m scripts.check_migrations static
    python -m scripts.check_migrations roundtrip --database-url postgresql+asyncpg://u:p@host/db

static     Fails when an upgrade() contains an operation that breaks the *previous* release while
           it is still serving traffic (rolling deploys run old and new code against the new schema):
           dropping or renaming columns and tables, changing a column type, setting NOT NULL, adding
           a NOT NULL column without a default. Put `# expand-contract: approved` in the migration
           file, with a comment explaining the plan, when the change is the "contract" half of an
           expand/contract sequence and the previous release no longer uses the thing.

roundtrip  Creates a scratch database on the server, runs upgrade head -> downgrade base ->
           upgrade head, and fails if the schema after the round trip differs from the first
           upgrade (a downgrade that leaves things behind, or an upgrade that is not repeatable).
           Also fails if the migrations and the SQLAlchemy models disagree (`alembic check`).
"""

import argparse
import ast
import asyncio
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"
APPROVAL_MARKER = "# expand-contract: approved"

# Raw SQL that is destructive for a running previous release.
_DESTRUCTIVE_SQL = [
    (re.compile(r"\bDROP\s+TABLE\b", re.I), "DROP TABLE"),
    (re.compile(r"\bDROP\s+COLUMN\b", re.I), "DROP COLUMN"),
    (re.compile(r"\bRENAME\s+(TO|COLUMN)\b", re.I), "RENAME"),
    (re.compile(r"\bALTER\s+COLUMN\b.*\bTYPE\b", re.I | re.S), "ALTER COLUMN ... TYPE"),
    (re.compile(r"\bSET\s+NOT\s+NULL\b", re.I), "SET NOT NULL"),
    (re.compile(r"\bDROP\s+TYPE\b", re.I), "DROP TYPE"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
]


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.message}"


def _op_name(call: ast.Call) -> str | None:
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "op"
    ):
        return func.attr
    return None


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_const(node: ast.expr | None, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _strings(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def check_source(source: str, filename: str = "<migration>") -> list[Finding]:
    """Return findings for destructive operations in upgrade(). Empty when approved or clean."""
    if APPROVAL_MARKER in source:
        return []
    tree = ast.parse(source, filename)
    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None
    )
    if upgrade is None:
        return [Finding(filename, 1, "migration has no upgrade() function")]

    findings: list[Finding] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call):
            continue
        name = _op_name(node)
        if name is None:
            continue
        line = node.lineno

        if name in {"drop_column", "drop_table", "rename_table"}:
            findings.append(Finding(filename, line, f"op.{name}() breaks the running release"))
        elif name == "alter_column":
            if _kwarg(node, "type_") is not None:
                findings.append(
                    Finding(filename, line, "op.alter_column(type_=...) rewrites or breaks readers")
                )
            if _kwarg(node, "new_column_name") is not None:
                findings.append(
                    Finding(filename, line, "renaming a column breaks the running release")
                )
            if _is_const(_kwarg(node, "nullable"), False):
                findings.append(
                    Finding(filename, line, "setting NOT NULL breaks writers that omit the column")
                )
        elif name == "add_column":
            column = node.args[1] if len(node.args) > 1 else None
            if isinstance(column, ast.Call):
                not_null = _is_const(_kwarg(column, "nullable"), False)
                has_default = (
                    _kwarg(column, "server_default") is not None
                    or _kwarg(column, "default") is not None
                )
                if not_null and not has_default:
                    findings.append(
                        Finding(
                            filename,
                            line,
                            "NOT NULL column without a default breaks the running release",
                        )
                    )
        elif name == "execute":
            for text in _strings(node):
                for pattern, label in _DESTRUCTIVE_SQL:
                    if pattern.search(text):
                        findings.append(Finding(filename, line, f"raw SQL contains {label}"))
                        break
    return findings


def check_versions(directory: Path = VERSIONS_DIR) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(directory.glob("*.py")):
        findings.extend(check_source(path.read_text(), path.name))
    return findings


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

_SNAPSHOT_QUERIES = {
    "columns": """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> 'alembic_version'
        ORDER BY table_name, column_name""",
    "indexes": "SELECT tablename, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename <> 'alembic_version' ORDER BY indexname",
    "constraints": """
        SELECT conrelid::regclass::text AS tbl, conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace AND conrelid::regclass::text <> 'alembic_version'
        ORDER BY conname""",
    "triggers": """
        SELECT tgrelid::regclass::text AS tbl, tgname, pg_get_triggerdef(oid) AS def
        FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgname""",
    "functions": """
        SELECT proname, pg_get_functiondef(oid) AS def
        FROM pg_proc WHERE pronamespace = 'public'::regnamespace ORDER BY proname""",
}


async def snapshot(url: str) -> dict[str, list[tuple]]:
    conn = await asyncpg.connect(url.replace("+asyncpg", ""))
    try:
        return {
            name: [tuple(r.values()) for r in await conn.fetch(query)]
            for name, query in _SNAPSHOT_QUERIES.items()
        }
    finally:
        await conn.close()


def _alembic(database_url: str, *args: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "ENVIRONMENT": "local",
        "DB_SSL_MODE": "disable",
    }
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, env=env, check=True)


def diff_snapshots(before: dict[str, list[tuple]], after: dict[str, list[tuple]]) -> list[str]:
    problems: list[str] = []
    for kind in before:
        left, right = set(before[kind]), set(after[kind])
        problems += [
            f"{kind}: lost after round trip: {row}" for row in sorted(left - right, key=str)
        ]
        problems += [
            f"{kind}: left behind after round trip: {row}" for row in sorted(right - left, key=str)
        ]
    return problems


async def roundtrip(admin_url: str) -> list[str]:
    parsed = make_url(admin_url)
    scratch = f"migcheck_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(
        parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    await admin.execute(f'CREATE DATABASE "{scratch}"')
    scratch_url = parsed.set(database=scratch).render_as_string(hide_password=False)
    try:
        loop = asyncio.get_running_loop()
        run = lambda *a: loop.run_in_executor(None, _alembic, scratch_url, *a)  # noqa: E731
        await run("upgrade", "head")
        first = await snapshot(scratch_url)
        await run("check")  # models and migrations agree
        await run("downgrade", "base")
        empty = await snapshot(scratch_url)
        await run("upgrade", "head")
        second = await snapshot(scratch_url)

        problems = diff_snapshots(first, second)
        leftovers = [
            f"{k}: survives downgrade to base: {row}" for k, rows in empty.items() for row in rows
        ]
        return problems + leftovers
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        await admin.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("static")
    rt = sub.add_parser("roundtrip")
    rt.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args(argv)

    if args.command == "static":
        findings = check_versions()
        for f in findings:
            print(f"ERROR {f}")
        if findings:
            print(
                f"\nDestructive migration. If this is the contract step, add `{APPROVAL_MARKER}`."
            )
            return 1
        print("migrations: no destructive operations")
        return 0

    if not args.database_url:
        print("--database-url (or DATABASE_URL) is required")
        return 2
    problems = asyncio.run(roundtrip(args.database_url))
    for p in problems:
        print(f"ERROR {p}")
    if problems:
        return 1
    print("migrations: upgrade/downgrade/upgrade round trip is clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
