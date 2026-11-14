"""
Postgres connection pool + migration runner.

Single async connection pool exposed via `get_pool()`. Migrations are applied
once on startup by reading every `server/sql/NNN_*.sql` in order and inserting
into `schema_migrations` on success.

If the DB is unreachable on startup, we retry with backoff and let FastAPI come
up anyway, then keep trying in the background (init_db_until_ready). Until it
answers, every route that needs it returns 503. With sign-in on that includes
read-aloud, whose caller is looked up in the database.
"""
import asyncio
import logging
import os
from pathlib import Path

from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from .services import doc_content

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = (
    "postgresql://natural_reader:natural_reader@127.0.0.1:5433/natural_reader"
)

# Measured: the planner overestimates `GET /v1/docs` rows ~12x (an OR of
# hashed subplans), so Postgres JIT — on by default in pgvector/pgvector:pg16
# once a query's estimated cost passes jit_above_cost=100000 — starts firing
# once the whole instance holds ~2.2k documents (+~10ms/request), and full
# inline/optimize kicks in around ~11k documents (+175-340ms/request). With
# JIT off the same query runs in ~10ms at 20k docs. This is an OLTP workload
# that never benefits from JIT compilation. Passed as connection `options` so
# it's scoped to this app's own pool — Keycloak's separate DB connections are
# unaffected.
POOL_CONN_KWARGS = {"options": "-c jit=off"}

_pool: AsyncConnectionPool | None = None
_pool_ready = asyncio.Event()

SQL_DIR = Path(__file__).parent / "sql"
CONNECT_TIMEOUT_S = 10.0
# One worker migrates at a time: a session advisory lock, so workers that start together queue here instead of
# running the same file twice. Any constant works; this one is "nrmg" in ASCII.
MIGRATION_LOCK = 0x6E726D67
LATE_RETRY_S = 15.0


class DatabaseUnavailable(RuntimeError):
    """The pool isn't open: Postgres was unreachable at start, or its migrations haven't finished. A
    RuntimeError, as get_pool always raised, and http_errors answers it with a 503."""


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


async def _configure(conn) -> None:
    """Runs once per new connection: register the pgvector codec, so Python lists pass where `vector(...)` is
    expected. On a fresh database the extension doesn't exist until the migrations create it, so the first
    connection defers; later ones register cleanly."""
    try:
        await register_vector_async(conn)
    except Exception as e:
        logger.debug("pgvector codec registration deferred: %s", e)


async def _after_migrations(pool: AsyncConnectionPool) -> None:
    """A crash mid-job leaves docs mid-state: make them resumable, then GC any content nothing references
    (A1 spec §3, §4). Same for 'converting' rows: their conversion job never got to finish."""
    async with pool.connection() as conn:
        await doc_content.recover_states(conn)
        await doc_content.sweep_orphans(conn)
        await conn.execute(
            "UPDATE documents SET conversion_state = NULL, conversion_progress = NULL, "
            "conversion_error = 'Interrupted by server restart', "
            "updated_at = now() WHERE conversion_state = 'converting'"
        )


async def _open_once(url: str) -> None:
    """One attempt: open a pool, migrate, then publish the pool. The pool becomes `_pool` only after the
    migrations and the start-up sweeps succeed, so is_ready() never says yes to a half-migrated schema; a
    failed attempt closes its pool instead of leaking it."""
    global _pool
    pool = AsyncConnectionPool(url, min_size=1, max_size=10, open=False, configure=_configure,
                               kwargs=POOL_CONN_KWARGS)
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_S)
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        await _run_migrations(pool)
        await bootstrap_admin(pool)
        await _after_migrations(pool)
    except BaseException:
        await pool.close()
        raise
    _pool = pool
    _pool_ready.set()


async def init_db(attempts: int = 5, first_delay: float = 1.0) -> bool:
    """
    Open the connection pool and apply pending migrations. Tries `attempts` times with exponential backoff.
    Returns True on success, False if the DB stayed unreachable (the app then keeps trying in the background:
    init_db_until_ready).
    """
    url = database_url()
    delay = first_delay
    for attempt in range(attempts):
        try:
            await _open_once(url)
            logger.info("Postgres pool opened (attempt %d)", attempt + 1)
            return True
        except Exception as e:
            logger.warning("DB connect attempt %d failed: %s", attempt + 1, e)
            if attempt + 1 < attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 16.0)

    logger.error("Postgres unreachable after %d attempts; routes that need it answer 503", attempts)
    return False


async def init_db_until_ready(on_ready, first_pause: float = 1.0, max_pause: float = LATE_RETRY_S) -> None:
    """For a database that wasn't there at start (Postgres still booting, or restarted later than the app):
    keep trying for as long as the process lives, soon at first and then every `max_pause` seconds, then run
    `on_ready` once, the start-up steps that needed it."""
    url = database_url()
    pause, tries = first_pause, 0
    while True:
        try:
            await _open_once(url)
            break
        except Exception as e:
            tries += 1
            # One line when it starts waiting, then quiet: this can go on for as long as Postgres is away.
            logger.log(logging.WARNING if tries == 1 else logging.DEBUG,
                       "Postgres still unreachable (try %d): %s", tries, e)
            await asyncio.sleep(pause)
            pause = min(pause * 2, max_pause)
    logger.info("Postgres came up late (after %d failed tries); finishing start-up", tries)
    await on_ready()


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
        raise DatabaseUnavailable("Database is not initialized")
    return _pool


def is_ready() -> bool:
    return _pool is not None


async def bootstrap_admin(pool: AsyncConnectionPool | None = None) -> None:
    """If BOOTSTRAP_ADMIN_EMAIL is set, point the seed admin's email at it so the
    real admin's first OIDC login links to (and inherits) that row. Idempotent;
    only ever touches the still-unlinked seed row."""
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    if not email:
        return
    pool = pool or get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'users'"
            )
            if not await cur.fetchone():
                return
        await conn.execute(
            "UPDATE users SET email = %s, updated_at = now() "
            "WHERE id = '00000000-0000-0000-0000-000000000001' AND oidc_sub IS NULL",
            (email,),
        )
    logger.info("Bootstrap admin email set to %s", email)


async def _run_migrations(pool: AsyncConnectionPool) -> None:
    """Apply pending `NNN_*.sql` files in order, each in its own transaction, so a failure keeps the files
    before it and the next start resumes at the one that failed. The advisory lock makes workers that start
    together take turns; the applied list is read after the lock, so the second worker finds nothing to do."""
    if not SQL_DIR.is_dir():
        logger.warning("No migrations directory at %s", SQL_DIR)
        return
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        return

    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        try:
            # Ensure schema_migrations exists even on a fresh DB; the first migration
            # creates it but we also need to query it to know which have been applied.
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in await cur.fetchall()}
            await conn.commit()   # or every file below would be a savepoint inside this one transaction

            for path in files:
                version = _version_from_filename(path.name)
                if version is None or version in applied:
                    continue
                logger.info("Applying migration %s", path.name)
                async with conn.transaction():
                    await conn.execute(path.read_text())
        finally:
            await conn.rollback()
            await conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
            await conn.commit()


def _version_from_filename(name: str) -> int | None:
    """`001_init.sql` -> 1. Returns None for files that don't match the pattern."""
    head = name.split("_", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None
