import pytest
from psycopg import errors as pg_errors

from server.tests import seed

pytestmark = pytest.mark.asyncio


async def test_project_and_membership_roundtrip(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o1','o@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'Proj') RETURNING id",
        (owner,))
    pid = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, owner))
    cur = await db_conn.execute(
        "SELECT count(*) FROM project_members WHERE project_id=%s", (pid,))
    assert (await cur.fetchone())[0] == 1


async def test_entries_hold_tags_and_documents_hold_no_owner(db_conn):
    # A1 §6: tags are personal (on the entry); content has no owner or
    # project column. (Dropped tables/columns are pinned by test_migration_011.)
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o2','o2@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    await seed.seed_doc(db_conn, "d1", owner, file_name="f.pdf", tags=("alpha", "beta"))
    cur = await db_conn.execute(
        "SELECT tags, added_via FROM library_entries WHERE doc_id='d1' AND user_id=%s", (owner,))
    tags, via = await cur.fetchone()
    assert set(tags) == {"alpha", "beta"} and via == "upload"
    for col in ("project_id", "user_id"):
        cur = await db_conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='documents' AND column_name=%s", (col,))
        assert await cur.fetchone() is None, col


async def test_entry_added_via_is_constrained(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o4','o4@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    await seed.seed_doc(db_conn, "d3", None)
    with pytest.raises(pg_errors.CheckViolation):
        async with db_conn.transaction():
            await db_conn.execute(
                "INSERT INTO library_entries (user_id, doc_id, added_via) "
                "VALUES (%s,'d3','granted')", (owner,))


async def test_project_documents_links_and_cascades(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o3','o3@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    pid = str((await cur.fetchone())[0])
    await seed.seed_doc(db_conn, "d2", owner, file_name="f.pdf", project_ids=[pid])
    cur = await db_conn.execute("SELECT added_by FROM project_documents WHERE doc_id='d2'")
    assert str((await cur.fetchone())[0]) == owner  # placements record who filed them
    await db_conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
    cur = await db_conn.execute("SELECT count(*) FROM project_documents WHERE doc_id='d2'")
    assert (await cur.fetchone())[0] == 0
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id='d2'")
    assert (await cur.fetchone())[0] == 1  # the doc survives its project (its entry holds it)
