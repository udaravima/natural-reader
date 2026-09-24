"""Test-database plumbing shared by conftest.py and migration tests that need
a *scratch* database. Lives outside conftest so tests can import it."""
from __future__ import annotations

import os
from pathlib import Path

from psycopg import AsyncConnection

TEST_DB = "natural_reader_test"
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql://natural_reader:natural_reader@localhost:5433/postgres",
)
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql://natural_reader:natural_reader@localhost:5433/{TEST_DB}",
)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def url_for(db_name: str) -> str:
    """Same server and credentials as ADMIN_URL, different database."""
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + db_name


async def apply_migrations(url: str, max_version: int | None = None) -> None:
    """Apply pending `NNN_*.sql` files in order, one transaction each — the same
    rules as server/db.py's runner. `max_version` stops early, so a migration
    test can build the schema as it was BEFORE the migration under test."""
    conn = await AsyncConnection.connect(url)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur = await conn.execute("SELECT version FROM schema_migrations")
        applied = {r[0] for r in await cur.fetchall()}
        await conn.commit()
        for path in sorted(SQL_DIR.glob("*.sql")):
            head = path.name.split("_", 1)[0]
            version = int(head) if head.isdigit() else None
            if version is None or version in applied:
                continue
            if max_version is not None and version > max_version:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
    finally:
        await conn.close()
