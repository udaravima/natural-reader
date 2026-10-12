"""The guarantees migration 018's constraints make (V1 spec §3.2)."""
import pytest
from psycopg import errors as pg_errors

from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2 = "1" * 64, "2" * 64


async def _setup(db_conn):
    alice = await member(db_conn, "sch-alice")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="a.pdf")
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name="b.pdf")
    return alice, pid


async def test_a_document_has_one_current_version(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    with pytest.raises(pg_errors.UniqueViolation):
        async with db_conn.transaction():
            await db_conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,2,%s,'b.pdf','upload',true,%s,true)",
                (pid, pdoc, C2, alice.user_id))


async def test_content_is_current_in_at_most_one_document_per_project(db_conn):
    alice, pid = await _setup(db_conn)
    await seed.place_doc(db_conn, pid, C1, alice.user_id)
    with pytest.raises(pg_errors.UniqueViolation):
        async with db_conn.transaction():
            await seed.place_doc(db_conn, pid, C1, alice.user_id)
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    await seed.place_doc(db_conn, other, C1, alice.user_id)  # another project: fine


async def test_a_version_cannot_belong_to_another_projects_document(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    with pytest.raises(pg_errors.ForeignKeyViolation):
        async with db_conn.transaction():
            await db_conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,2,%s,'b.pdf','upload',false,%s,true)",
                (other, pdoc, C2, alice.user_id))


async def test_a_note_is_at_most_500_characters(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    with pytest.raises(pg_errors.CheckViolation):
        async with db_conn.transaction():
            await seed.add_version(db_conn, pdoc, C2, alice.user_id, note="x" * 501)


async def test_removing_a_document_removes_its_versions_not_held_content(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, pdoc, C2, alice.user_id)
    await db_conn.execute("DELETE FROM project_docs WHERE id = %s", (pdoc,))
    cur = await db_conn.execute("SELECT count(*) FROM project_documents WHERE project_id = %s", (pid,))
    assert (await cur.fetchone())[0] == 0
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id IN (%s,%s)", (C1, C2))
    assert (await cur.fetchone())[0] == 2  # alice's entries still hold both


async def test_seed_names_a_placement_like_migration_018(db_conn):
    alice, pid = await _setup(db_conn)
    await db_conn.execute("UPDATE library_entries SET file_name = 'Mine.pdf' "
                          "WHERE user_id = %s AND doc_id = %s", (alice.user_id, C1))
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    cur = await db_conn.execute(
        "SELECT version, file_name, is_current FROM project_documents WHERE project_doc_id = %s",
        (pdoc,))
    assert await cur.fetchone() == (1, "Mine.pdf", True)
    n = await seed.add_version(db_conn, pdoc, C2, alice.user_id)
    cur = await db_conn.execute(
        "SELECT version, is_current FROM project_documents WHERE project_doc_id = %s "
        "ORDER BY version", (pdoc,))
    assert n == 2 and await cur.fetchall() == [(1, False), (2, True)]
