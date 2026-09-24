"""Shared pytest fixtures.

The DB fixtures need a running Postgres — bring it up first:

    env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres   # (podman)
    #  or: docker-compose up -d postgres

They use a separate `natural_reader_test` database and roll every test back, so
dev data is never touched.

Design note: migrations are applied once per session in a *sync* fixture via
`asyncio.run` (its own throwaway loop), and each test gets a fresh connection
opened in that test's own event loop. This sidesteps the pytest-asyncio 1.x trap
where a session-scoped async fixture's objects are bound to a loop the
function-scoped test can't reuse.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from server.tests.dbutil import ADMIN_URL as _ADMIN_URL, TEST_DB, TEST_URL, apply_migrations


async def _ensure_test_db() -> None:
    # CREATE DATABASE can't run in a transaction — use autocommit.
    conn = await AsyncConnection.connect(_ADMIN_URL, autocommit=True)
    try:
        cur = await conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
        )
        if await cur.fetchone() is None:
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    """Ensure the test DB exists and all migrations are applied, once."""
    asyncio.run(_ensure_test_db())
    asyncio.run(apply_migrations(TEST_URL))


@pytest_asyncio.fixture
async def db_conn():
    """A connection wrapped in a transaction that is always rolled back."""
    conn = await AsyncConnection.connect(TEST_URL)
    tx = conn.transaction(force_rollback=True)
    await tx.__aenter__()
    try:
        yield conn
    finally:
        await tx.__aexit__(None, None, None)
        await conn.close()
