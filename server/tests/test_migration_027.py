"""Migration 027 (the indexing queue): the table, its CHECKs, and a Down that can be run and undone."""
import re
from pathlib import Path

import pytest
from psycopg.errors import CheckViolation

from server.tests import seed

pytestmark = pytest.mark.asyncio
SQL = Path(__file__).parents[1] / "sql" / "027_index_queue.sql"
A = "a" * 64


async def test_the_job_and_state_checks_refuse_other_values(db_conn):
    await seed.seed_doc(db_conn, A, None, state="stored")
    await db_conn.execute("INSERT INTO doc_index_jobs (doc_id, job) VALUES (%s, 'convert')", (A,))
    for column, value in (("job", "rebuild"), ("state", "failed")):
        with pytest.raises(CheckViolation):
            async with db_conn.transaction():
                await db_conn.execute(f"UPDATE doc_index_jobs SET {column} = %s", (value,))


async def test_the_down_in_the_header_runs_and_the_file_puts_it_back(db_conn):
    text = SQL.read_text()
    down = [line.removeprefix("--").strip() for line in text.splitlines() if re.match(r"--\s+(DROP|DELETE|ALTER) ", line)]
    assert len(down) == 3
    async with db_conn.transaction():
        for statement in down:
            await db_conn.execute(statement)
        cur = await db_conn.execute("SELECT to_regclass('doc_index_jobs')")
        assert (await cur.fetchone())[0] is None
        await db_conn.execute(text)
    cur = await db_conn.execute("SELECT to_regclass('doc_index_jobs')::text, "
                                "(SELECT count(*) FROM schema_migrations WHERE version = 27)")
    assert await cur.fetchone() == ("doc_index_jobs", 1)


async def test_doc_rebuilds_counts_its_workers_stopping(db_conn):
    cur = await db_conn.execute("SELECT column_default FROM information_schema.columns "
                                "WHERE table_name = 'doc_rebuilds' AND column_name = 'stops'")
    assert (await cur.fetchone())[0] == "0"
