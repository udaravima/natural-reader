"""Migration 017 (A0 §3): roles on memberships, people's names, project
limits and the activity feed. Membership becomes the only access source; a
project outlives its creator."""
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import NotNullViolation

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


async def test_017_backfills_roles_and_projects_outlive_their_creator():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url, max_version=16)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            alice, bob, carol = [await _user(conn, s) for s in ("alice", "bob", "carol")]
            p1 = (await _one(conn, "INSERT INTO projects (owner_user_id, name) "
                                   "VALUES (%s,'P1') RETURNING id", (alice,)))[0]
            p2 = (await _one(conn, "INSERT INTO projects (owner_user_id, name) "
                                   "VALUES (%s,'P2') RETURNING id", (carol,)))[0]
            # alice owns P1 AND has a member row; bob is a plain member;
            # carol owns P2 with no member row (the pre-A0 two-sources case).
            await conn.execute(
                "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s),(%s,%s)",
                (p1, alice, p1, bob))

            async with conn.transaction():  # one transaction, like server/db.py
                await conn.execute((SQL_DIR / "017_project_roles.sql").read_text())

            cur = await conn.execute("SELECT project_id, user_id, role, added_by FROM project_members")
            rows = {(str(r[0]), str(r[1])): (r[2], r[3]) for r in await cur.fetchall()}
            assert rows[(str(p1), str(alice))][0] == "owner"
            assert rows[(str(p1), str(bob))][0] == "contributor"
            assert rows[(str(p2), str(carol))] == ("owner", carol)
            cur = await conn.execute(
                "SELECT project_id, kind, details->>'name', (details->>'backfilled')::bool "
                "FROM project_events")
            assert sorted((str(r[0]), r[1], r[2], r[3]) for r in await cur.fetchall()) == sorted(
                [(str(p1), "project.created", "P1", True), (str(p2), "project.created", "P2", True)])
            with pytest.raises(NotNullViolation):  # the backfill default is gone
                await conn.execute(
                    "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (p2, bob))
            # Deleting the creator keeps the project, ownerless, with its history.
            await conn.execute("DELETE FROM users WHERE id = %s", (carol,))
            assert await _one(conn, "SELECT created_by FROM projects WHERE id = %s", (p2,)) == (None,)
            assert (await _one(conn, "SELECT count(*) FROM project_members "
                                     "WHERE project_id = %s", (p2,)))[0] == 0
            assert (await _one(conn, "SELECT count(*) FROM project_events "
                                     "WHERE project_id = %s", (p2,)))[0] == 1
            await conn.execute(
                "UPDATE users SET username='al', first_name='Al', last_name='Ice', "
                "project_limit=3 WHERE id=%s", (alice,))
            assert (await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 17")) == (1,)
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_every_migration_records_its_version():
    # The runner (server/db.py) never records versions itself: a file that
    # forgets its INSERT re-runs on every startup. 017 is not idempotent
    # (a column rename), so a re-run would stop the server from starting.
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            cur = await conn.execute("SELECT version FROM schema_migrations")
            recorded = {r[0] for r in await cur.fetchall()}
        finally:
            await conn.close()
        files = {int(p.name.split("_", 1)[0]) for p in SQL_DIR.glob("*.sql")
                 if p.name.split("_", 1)[0].isdigit()}
        assert recorded == files
    finally:
        await _drop(admin, name)
