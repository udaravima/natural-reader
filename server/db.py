"""
Postgres connection pool + migration runner.

Single async connection pool exposed via `get_pool()`. Migrations are applied
once on startup by reading every `server/sql/NNN_*.sql` in order and inserting
into `schema_migrations` on success.

If the DB is unreachable on startup, we retry with backoff and ultimately let
FastAPI come up anyway — TTS stays available; chat/doc routes will return 503.
"""
import asyncio
import logging
import os
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = (
    "postgresql://natural_reader:natural_reader@localhost:5433/natural_reader"
)

_pool: AsyncConnectionPool | None = None
_pool_ready = asyncio.Event()


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


async def init_db() -> bool:
    """
    Open the connection pool and apply pending migrations. Retries 5 times with
    exponential backoff before giving up. Returns True on success, False if the
    DB stayed unreachable.
    """
    global _pool

    url = database_url()
    delay = 1.0
    for attempt in range(5):
        try:
            pool = AsyncConnectionPool(url, min_size=1, max_size=10, open=False)
            await pool.open(wait=True, timeout=10)
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
            _pool = pool
            _pool_ready.set()
            logger.info("Postgres pool opened (attempt %d)", attempt + 1)
            await _run_migrations()
            return True
        except Exception as e:
            logger.warning("DB connect attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 16.0)

    logger.error("Postgres unreachable after 5 attempts — chat/doc routes will 503")
    return False


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_ready.clear()


def get_pool() -> AsyncConnectionPool:
    """
    Return the active pool, or raise if init_db() never succeeded. Routes should
    catch and translate to HTTP 503.
    """
    if _pool is None:
        raise RuntimeError("Database is not initialized")
    return _pool


def is_ready() -> bool:
    return _pool is not None


async def _run_migrations() -> None:
    sql_dir = Path(__file__).parent / "sql"
    if not sql_dir.is_dir():
        logger.warning("No migrations directory at %s", sql_dir)
        return

    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        return

    pool = get_pool()
    async with pool.connection() as conn:
        # Ensure schema_migrations exists even on a fresh DB; the first migration
        # creates it but we also need to query it to know which have been applied.
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        async with conn.cursor() as cur:
            await cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in await cur.fetchall()}

        for path in files:
            version = _version_from_filename(path.name)
            if version is None or version in applied:
                continue
            sql = path.read_text()
            logger.info("Applying migration %s", path.name)
            async with conn.transaction():
                await conn.execute(sql)


def _version_from_filename(name: str) -> int | None:
    """`001_init.sql` -> 1. Returns None for files that don't match the pattern."""
    head = name.split("_", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None
