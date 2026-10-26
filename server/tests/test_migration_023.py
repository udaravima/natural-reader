"""Migration 023 (admin console v2 phase 3): inference_usage rows are per user,
day, model and kind. Rows written before the upgrade survive as model ''/kind ''
so nobody's budget for the day changes mid-day."""
import secrets
from datetime import date, timedelta

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, NotNullViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _user(conn, sub):
    return (await _one(
        conn,
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i', %s, %s, 'member', 'active') RETURNING id", (sub, f"{sub}@x.io")))[0]


async def _scratch_db():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'CREATE DATABASE "{name}"')
    return admin, name


async def _drop(admin, name):
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _migrated_from_022():
    """A scratch DB at version 22 holding two usage rows for one user, then upgraded."""
    admin, name = await _scratch_db()
    url = url_for(name)
    await apply_migrations(url, max_version=22)
    conn = await AsyncConnection.connect(url, autocommit=True)
    alice = await _user(conn, "alice")
    today = date.today()
    for day, p, e, r in ((today, 10, 5, 2), (today - timedelta(days=1), 7, 3, 1)):
        await conn.execute(
            "INSERT INTO inference_usage (user_id, day, prompt_tokens, eval_tokens, requests) "
            "VALUES (%s,%s,%s,%s,%s)", (alice, day, p, e, r))
    await apply_migrations(url)
    return admin, name, conn, alice, today


async def test_023_keeps_old_rows_as_unknown_model_and_kind():
    admin, name, conn, alice, today = await _migrated_from_022()
    try:
        cur = await conn.execute(
            "SELECT day, model, kind, prompt_tokens, eval_tokens, requests, estimated_tokens "
            "FROM inference_usage WHERE user_id = %s ORDER BY day DESC", (alice,))
        assert await cur.fetchall() == [
            (today, "", "", 10, 5, 2, 0),
            (today - timedelta(days=1), "", "", 7, 3, 1, 0),
        ]
        assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 23") == (1,)
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_023_primary_key_is_user_day_model_kind():
    admin, name, conn, alice, today = await _migrated_from_022()
    try:
        cur = await conn.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
            "WHERE i.indrelid = 'inference_usage'::regclass AND i.indisprimary "
            "ORDER BY k.ord")
        assert [r[0] for r in await cur.fetchall()] == ["user_id", "day", "model", "kind"]
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_023_kind_accepts_only_chat_reader_or_empty():
    admin, name, conn, alice, today = await _migrated_from_022()
    try:
        with pytest.raises(CheckViolation):
            await conn.execute(
                "INSERT INTO inference_usage (user_id, day, model, kind) VALUES (%s,%s,'m','other')",
                (alice, today))
        for kind in ("chat", "reader"):
            await conn.execute(
                "INSERT INTO inference_usage (user_id, day, model, kind) VALUES (%s,%s,'m',%s)",
                (alice, today, kind))
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_023_drops_the_defaults_so_a_forgetful_writer_fails_loudly():
    admin, name, conn, alice, today = await _migrated_from_022()
    try:
        with pytest.raises(NotNullViolation):
            await conn.execute(
                "INSERT INTO inference_usage (user_id, day, prompt_tokens) VALUES (%s,%s,1)",
                (alice, today))
        with pytest.raises(NotNullViolation):
            await conn.execute(
                "INSERT INTO inference_usage (user_id, day, model) VALUES (%s,%s,'m')",
                (alice, today))
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_023_indexes_day_and_is_safe_to_run_twice():
    admin, name, conn, alice, today = await _migrated_from_022()
    try:
        assert await _one(
            conn, "SELECT 1 FROM pg_indexes WHERE tablename = 'inference_usage' "
                  "AND indexname = 'inference_usage_day_idx'") == (1,)
        await conn.execute((SQL_DIR / "023_usage_by_model.sql").read_text())
        assert (await _one(conn, "SELECT count(*) FROM inference_usage"))[0] == 2
    finally:
        await conn.close()
        await _drop(admin, name)
