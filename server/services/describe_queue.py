"""Holdings waiting for the helper model to write their tags and description (helper spec §5.1, §5.3; table
doc_describe_jobs, migration 028).

A job is for one holding: a project document's version holding `doc_id` (`project_doc_id`), or a person's library
entry for it (`user_id`). What the helper writes goes on that holding alone, so the vocabulary it was shown never
reaches another project or person holding the same file.

The rules follow the indexing queue's (index_queue.py), with these differences:
- A job is claimed only while doc_index_jobs is empty, and only once its content is indexed: indexing and
  conversion always go first, so the helper never slows them, and a job queued at upload waits for its text.
- One describe runs at a time across every worker (the claim takes a transaction advisory lock and looks for a live
  claim first): a second helper call would only queue up inside the same model server.
- A project job whose version is no longer current is dropped when a claim is looked for: the new version has a job
  of its own.
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
_CURRENT_VERSION = ("EXISTS (SELECT 1 FROM project_documents _v WHERE _v.project_doc_id = {j}.project_doc_id "
                    "AND _v.doc_id = {j}.doc_id AND _v.is_current AND _v.verified)")


@dataclass(frozen=True)
class Claim:
    id: int
    doc_id: str                  # the content read
    project_doc_id: str | None   # a project document's version holding doc_id ...
    user_id: str | None          # ... or this person's library entry for it
    requested_by: str | None     # charged for the call
    attempts: int                # this claim included


async def enqueue(conn, doc_id: str, *, project_doc_id: str | None = None, user_id: str | None = None,
                  requested_by: str | None) -> bool:
    """Queue one holding of `doc_id`: a project document's version, or a person's entry (exactly one of the two).
    A holding already waiting keeps its place and its first requester. True when it wrote."""
    if (project_doc_id is None) == (user_id is None):
        raise ValueError("a describe job is for a project document or a library entry")
    cur = await conn.execute(
        "INSERT INTO doc_describe_jobs (doc_id, project_doc_id, user_id, requested_by) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING RETURNING id", (doc_id, project_doc_id, user_id, requested_by))
    return await cur.fetchone() is not None


async def claim_next(conn, worker: str) -> Claim | None:
    """Claim the oldest waiting job (or one whose worker died) whose content is indexed, for `worker`, unless
    indexing or conversion jobs exist or another describe is running. Drops waiting project jobs whose version is
    no longer current first."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LANE_LOCK,))
    await conn.execute(
        f"DELETE FROM doc_describe_jobs j WHERE j.project_doc_id IS NOT NULL AND NOT ({LIVE}) "
        f"AND NOT {_CURRENT_VERSION.format(j='j')}")
    cur = await conn.execute(
        "UPDATE doc_describe_jobs SET claimed_by = %s, claimed_at = now(), attempts = attempts + 1 "
        "WHERE id = (SELECT j.id FROM doc_describe_jobs j JOIN documents d ON d.doc_id = j.doc_id "
        f"  WHERE NOT ({LIVE}) AND d.state = 'indexed' "
        "    AND NOT EXISTS (SELECT 1 FROM doc_index_jobs) "
        f"   AND NOT EXISTS (SELECT 1 FROM doc_describe_jobs WHERE {LIVE}) "
        "  ORDER BY j.queued_at, j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED) "
        "RETURNING id, doc_id, project_doc_id::text, user_id::text, requested_by::text, attempts", (worker,))
    row = await cur.fetchone()
    return Claim(*row) if row else None


async def heartbeat(conn, job_id: int, worker: str) -> None:
    await conn.execute("UPDATE doc_describe_jobs SET claimed_at = now() WHERE id = %s AND claimed_by = %s",
                       (job_id, worker))


async def finish(conn, job_id: int, worker: str) -> bool:
    """The job is over: the row goes, if `worker` still owns it."""
    cur = await conn.execute("DELETE FROM doc_describe_jobs WHERE id = %s AND claimed_by = %s RETURNING id",
                             (job_id, worker))
    return await cur.fetchone() is not None


async def retry(conn, job_id: int, worker: str) -> bool:
    """A failed try: back to the end of the queue, the try counted, if `worker` still owns it."""
    cur = await conn.execute(
        "UPDATE doc_describe_jobs SET claimed_by = NULL, claimed_at = NULL, queued_at = now() "
        "WHERE id = %s AND claimed_by = %s RETURNING id", (job_id, worker))
    return await cur.fetchone() is not None


async def release(conn, job_id: int, worker: str) -> bool:
    """Hand a running job back uncounted: the worker is shutting down, not dying."""
    cur = await conn.execute(
        "UPDATE doc_describe_jobs SET claimed_by = NULL, claimed_at = NULL, attempts = greatest(attempts - 1, 0) "
        "WHERE id = %s AND claimed_by = %s RETURNING id", (job_id, worker))
    return await cur.fetchone() is not None


async def drop_waiting(conn) -> int:
    """Remove every job no worker is on (the helper was turned off). Returns how many."""
    cur = await conn.execute(f"DELETE FROM doc_describe_jobs WHERE NOT ({LIVE})")
    return cur.rowcount


async def move(conn, from_project_doc: str, into_project_doc: str) -> None:
    """A merge moved `from`'s versions into `into` (spec §5.5): their waiting jobs follow, unless `into` already has
    one for the same content."""
    await conn.execute(
        "UPDATE doc_describe_jobs j SET project_doc_id = %s WHERE j.project_doc_id = %s AND NOT EXISTS ("
        "SELECT 1 FROM doc_describe_jobs o WHERE o.project_doc_id = %s AND o.doc_id = j.doc_id)",
        (into_project_doc, from_project_doc, into_project_doc))


async def waiting_versions(conn, project_doc_ids: list[str]) -> set[str]:
    """Which of these project documents have a job for their current version, waiting or running, whose content is
    indexed (until then the row says it is indexing; a failed one says why)."""
    if not project_doc_ids:
        return set()
    cur = await conn.execute(
        "SELECT j.project_doc_id::text FROM doc_describe_jobs j JOIN documents d ON d.doc_id = j.doc_id "
        f"WHERE j.project_doc_id = ANY(%s::uuid[]) AND d.state = 'indexed' AND {_CURRENT_VERSION.format(j='j')}",
        ([str(i) for i in project_doc_ids],))
    return {r[0] for r in await cur.fetchall()}


async def waiting_entries(conn, user_id: str, doc_ids: list[str]) -> set[str]:
    """Which of `doc_ids` have a job for `user_id`'s entry, waiting or running, whose content is indexed."""
    if not doc_ids:
        return set()
    cur = await conn.execute(
        "SELECT j.doc_id FROM doc_describe_jobs j JOIN documents d ON d.doc_id = j.doc_id "
        "WHERE j.user_id = %s AND j.doc_id = ANY(%s) AND d.state = 'indexed'", (user_id, list(doc_ids)))
    return {r[0] for r in await cur.fetchall()}
