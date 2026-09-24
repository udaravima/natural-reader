"""JIT is disabled on every pool connection (server/db.py POOL_CONN_KWARGS) —
see that constant's docstring for the measured numbers. This test proves the
`-c jit=off` option string is valid libpq syntax and actually takes effect,
independent of the pool itself (which needs a live app to exercise)."""
import pytest
from psycopg import AsyncConnection

from server.db import POOL_CONN_KWARGS
from server.tests.dbutil import TEST_URL

pytestmark = pytest.mark.asyncio


async def test_pool_conn_kwargs_disable_jit():
    conn = await AsyncConnection.connect(TEST_URL, **POOL_CONN_KWARGS)
    try:
        cur = await conn.execute("SHOW jit")
        row = await cur.fetchone()
        assert row[0] == "off"
    finally:
        await conn.close()
