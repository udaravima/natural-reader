"""Starting the database: a late Postgres, a failed migration, several workers migrating at once.

Every test that opens the pool points it at a scratch database made here, never at the shared test one."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import AsyncConnection

from server import db
from server.health import health_payload
from server.http_errors import install_handlers
from server.tests.dbutil import ADMIN_URL, url_for

DEAD_URL = "postgresql://nobody:nobody@127.0.0.1:1/none?connect_timeout=1"


@pytest_asyncio.fixture
async def scratch(monkeypatch):
    """An empty database, and db.py pointed at it with the after-migration sweeps switched off (they would
    look at the real storage folder)."""
    name = f"nr_boot_{os.getpid()}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    await admin.execute(f'CREATE DATABASE "{name}"')
    monkeypatch.setenv("DATABASE_URL", url_for(name))
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)

    async def nothing(*_a, **_k):
        return None
    monkeypatch.setattr(db, "_after_migrations", nothing)
    try:
        yield url_for(name)
    finally:
        await db.close_db()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def _sql_dir(tmp_path: Path, monkeypatch, files: dict[str, str]) -> None:
    for name, sql in files.items():
        (tmp_path / name).write_text(sql)
    monkeypatch.setattr(db, "SQL_DIR", tmp_path)


ONE = ("CREATE TABLE IF NOT EXISTS schema_migrations (version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL "
       "DEFAULT now()); CREATE TABLE one (id INT); INSERT INTO schema_migrations(version) VALUES (1);")


async def test_the_pool_is_not_ready_until_the_migrations_have_run(scratch, tmp_path, monkeypatch):
    _sql_dir(tmp_path, monkeypatch, {"001_one.sql": ONE})
    seen = []
    real = db._run_migrations

    async def watching(pool):
        seen.append(db.is_ready())
        await real(pool)
    monkeypatch.setattr(db, "_run_migrations", watching)
    assert await db.init_db(attempts=1) is True
    assert seen == [False] and db.is_ready()


async def test_a_failed_migration_leaves_the_database_not_ready_and_closes_its_pool(scratch, tmp_path, monkeypatch):
    _sql_dir(tmp_path, monkeypatch, {"001_one.sql": ONE, "002_bad.sql": "SELECT no_such_function();"})
    pools = []
    real = db._run_migrations

    async def keeping(pool):
        pools.append(pool)
        await real(pool)
    monkeypatch.setattr(db, "_run_migrations", keeping)
    assert await db.init_db(attempts=1) is False
    assert not db.is_ready()
    assert pools and pools[0].closed
    with pytest.raises(db.DatabaseUnavailable):
        db.get_pool()


async def test_each_migration_commits_on_its_own(scratch, tmp_path, monkeypatch):
    # 001 succeeds and 002 fails: 001 stays applied, as ARCHITECTURE.md says, so the next start resumes at 002.
    _sql_dir(tmp_path, monkeypatch, {"001_one.sql": ONE, "002_bad.sql": "SELECT no_such_function();"})
    assert await db.init_db(attempts=1) is False
    conn = await AsyncConnection.connect(scratch)
    try:
        cur = await conn.execute("SELECT version FROM schema_migrations")
        assert [r[0] for r in await cur.fetchall()] == [1]
    finally:
        await conn.close()


async def test_a_second_worker_waits_for_the_first_ones_migrations(scratch, tmp_path, monkeypatch):
    _sql_dir(tmp_path, monkeypatch, {"001_one.sql": ONE})
    holder = await AsyncConnection.connect(scratch, autocommit=True)
    try:
        await holder.execute("SELECT pg_advisory_lock(%s)", (db.MIGRATION_LOCK,))
        starting = asyncio.create_task(db.init_db(attempts=1))
        await asyncio.sleep(0.5)
        assert not starting.done() and not db.is_ready()
        await holder.execute("SELECT pg_advisory_unlock(%s)", (db.MIGRATION_LOCK,))
        assert await asyncio.wait_for(starting, 10) is True
    finally:
        await holder.close()


async def test_workers_starting_together_all_finish_without_an_error(scratch, tmp_path, monkeypatch):
    _sql_dir(tmp_path, monkeypatch, {"001_one.sql": ONE})
    from psycopg_pool import AsyncConnectionPool
    pools = [AsyncConnectionPool(scratch, min_size=1, max_size=2, open=False) for _ in range(4)]
    try:
        for p in pools:
            await p.open(wait=True, timeout=10)
        await asyncio.gather(*(db._run_migrations(p) for p in pools))   # CREATE TABLE one twice would raise
    finally:
        for p in pools:
            await p.close()


async def test_a_database_that_comes_up_late_is_picked_up(monkeypatch, caplog):
    calls, ready = [], []

    async def attempt(url):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("connection refused")
    monkeypatch.setattr(db, "_open_once", attempt)

    async def on_ready():
        ready.append(True)
    with caplog.at_level("WARNING", logger="server.db"):
        await asyncio.wait_for(db.init_db_until_ready(on_ready, first_pause=0.01), 5)
    assert len(calls) == 3 and ready == [True]
    assert caplog.text.count("still unreachable") == 1      # one line, not one per try


async def test_no_database_is_a_503_not_a_500(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    app = FastAPI()
    install_handlers(app)

    @app.get("/x")
    async def x():
        db.get_pool()
    r = TestClient(app, raise_server_exceptions=False).get("/x")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "db_unavailable"


async def test_an_unreachable_database_gives_up_after_its_attempts(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DEAD_URL)
    monkeypatch.setattr(db, "CONNECT_TIMEOUT_S", 0.5)
    assert await db.init_db(attempts=1, first_delay=0) is False and not db.is_ready()


def test_health_says_whether_the_database_is_up():
    assert health_payload(model_loaded=True, db_ready=True) == {"status": "ok", "model_loaded": True, "database": "ok"}
    down = health_payload(model_loaded=True, db_ready=False)
    # Read-aloud's own answer stays "ok" (the reader's Kokoro switch reads `status`); the database has its own field.
    assert down["status"] == "ok" and down["database"] == "unavailable"
    assert health_payload(model_loaded=False, db_ready=True)["status"] == "error"


def test_the_app_keeps_trying_a_late_database_and_reports_it():
    app_src = (Path(__file__).parents[1] / "app.py").read_text()
    assert "init_db_until_ready" in app_src
    # One attempt at start, so a missing database delays serving by one connect timeout, not five with backoff.
    assert "await init_db(attempts=1)" in app_src
    assert "health_payload" in (Path(__file__).parents[1] / "endpoints.py").read_text()
