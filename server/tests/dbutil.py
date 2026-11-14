"""Test-database plumbing shared by conftest.py and migration tests that need
a *scratch* database. Lives outside conftest so tests can import it."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict

TEST_DB = "natural_reader_test"
DEFAULT_ADMIN_URL = "postgresql://natural_reader:natural_reader@127.0.0.1:5433/postgres"
DEFAULT_TEST_URL = f"postgresql://natural_reader:natural_reader@127.0.0.1:5433/{TEST_DB}"
ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", DEFAULT_ADMIN_URL)
TEST_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def url_for(db_name: str) -> str:
    """Same server and credentials as ADMIN_URL, different database."""
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + db_name


def _server(url: str) -> tuple[str, str]:
    info = conninfo_to_dict(url)
    host = str(info.get("host") or "127.0.0.1").lower()
    return ("127.0.0.1" if host in {"localhost", "::1"} else host, str(info.get("port") or 5432))


def refuse_app_server(env: Mapping[str, str], *, test_url: str = TEST_URL, admin_url: str = ADMIN_URL) -> None:
    """The suite creates and drops databases, so it must not run on the Postgres server the app uses: on a
    deployment that server holds the real library and Keycloak. The defaults above ARE that server, so a bare
    `pytest` stops here. Raises with what to set."""
    if env.get("TEST_DB_ALLOW_APP_SERVER") == "1":
        return
    from server.db import DEFAULT_DATABASE_URL
    app = _server(env.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    for name, url in (("TEST_DATABASE_URL", test_url), ("TEST_ADMIN_DATABASE_URL", admin_url)):
        if _server(url) == app:
            raise RuntimeError(
                f"{name} points at the app's own Postgres ({app[0]}:{app[1]}). The tests create and drop "
                "databases, so run them on a separate server: set TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL "
                "(docs/TESTING.md, \"A scratch database\"). If this machine has only a development Postgres "
                "with nothing to lose, set TEST_DB_ALLOW_APP_SERVER=1.")


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
