"""Documents waiting for the helper model to write their tags and description (helper spec §5.3; table
doc_describe_jobs, migration 028).

The rules follow the indexing queue's (index_queue.py), with three differences:
- A job is claimed only while doc_index_jobs is empty: indexing and conversion always go first, so the helper never
  slows them.
- One describe runs at a time across every worker (the claim takes a transaction advisory lock and looks for a live
  claim first): a second helper call would only queue up inside the same model server.
- A row is queued while `claimed_by` is NULL. A job is tried twice (MAX_ATTEMPTS); a failed first try goes to the
  back of the queue.

Every function runs in the caller's transaction and doesn't commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from .rebuild_queue import CLAIM_LAPSE

MAX_ATTEMPTS = 2
_LANE_LOCK = "describe_lane"
LIVE = f"claimed_by IS NOT NULL AND claimed_at >= now() - interval '{CLAIM_LAPSE}'"   # a worker is on it now


@dataclass(frozen=True)
class Claim:
    doc_id: str
    requested_by: str | None   # charged for the call, and whose tags are shown when there is no project
    project_id: str | None     # whose tags the model is shown to reuse
    attempts: int              # this claim included


async def enqueue(conn, doc_id: str, *, requested_by: str | None, project_id: str | None) -> bool:
    """Queue `doc_id`. A document already waiting keeps its place and its first requester. True when it wrote."""
    cur = await conn.execute(
        "INSERT INTO doc_describe_jobs (doc_id, requested_by, project_id) VALUES (%s, %s, %s) "
        "ON CONFLICT (doc_id) DO NOTHING RETURNING doc_id", (doc_id, requested_by, project_id))
    return await cur.fetchone() is not None


async def claim_next(conn, worker: str) -> Claim | None:
    """Claim the oldest waiting job (or one whose worker died) for `worker`, unless indexing or conversion jobs
    exist or another describe is running."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LANE_LOCK,))
    cur = await conn.execute(
        "UPDATE doc_describe_jobs SET claimed_by = %s, claimed_at = now(), attempts = attempts + 1 "
        "WHERE doc_id = (SELECT doc_id FROM doc_describe_jobs "
        f"  WHERE NOT ({LIVE}) "
        "    AND NOT EXISTS (SELECT 1 FROM doc_index_jobs) "
        f"   AND NOT EXISTS (SELECT 1 FROM doc_describe_jobs WHERE {LIVE}) "
        "  ORDER BY queued_at, doc_id LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING doc_id, requested_by::text, project_id::text, attempts", (worker,))
    row = await cur.fetchone()
    return Claim(*row) if row else None


async def heartbeat(conn, doc_id: str, worker: str) -> None:
    await conn.execute("UPDATE doc_describe_jobs SET claimed_at = now() WHERE doc_id = %s AND claimed_by = %s",
                       (doc_id, worker))


async def finish(conn, doc_id: str, worker: str) -> bool:
    """The job is over: the row goes, if `worker` still owns it."""
    cur = await conn.execute("DELETE FROM doc_describe_jobs WHERE doc_id = %s AND claimed_by = %s RETURNING doc_id",
                             (doc_id, worker))
    return await cur.fetchone() is not None


async def retry(conn, doc_id: str, worker: str) -> bool:
    """A failed try: back to the end of the queue, the try counted, if `worker` still owns it."""
    cur = await conn.execute(
        "UPDATE doc_describe_jobs SET claimed_by = NULL, claimed_at = NULL, queued_at = now() "
        "WHERE doc_id = %s AND claimed_by = %s RETURNING doc_id", (doc_id, worker))
    return await cur.fetchone() is not None


async def release(conn, doc_id: str, worker: str) -> bool:
    """Hand a running job back uncounted: the worker is shutting down, not dying."""
    cur = await conn.execute(
        "UPDATE doc_describe_jobs SET claimed_by = NULL, claimed_at = NULL, attempts = greatest(attempts - 1, 0) "
        "WHERE doc_id = %s AND claimed_by = %s RETURNING doc_id", (doc_id, worker))
    return await cur.fetchone() is not None


async def drop_waiting(conn) -> int:
    """Remove every job no worker is on (the helper was turned off). Returns how many."""
    cur = await conn.execute(f"DELETE FROM doc_describe_jobs WHERE NOT ({LIVE})")
    return cur.rowcount


async def waiting(conn, doc_ids: list[str]) -> set[str]:
    """Which of `doc_ids` have a describe job, waiting or running."""
    if not doc_ids:
        return set()
    cur = await conn.execute("SELECT doc_id FROM doc_describe_jobs WHERE doc_id = ANY(%s)", (list(doc_ids),))
    return {r[0] for r in await cur.fetchall()}
