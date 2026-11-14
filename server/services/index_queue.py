"""The queue of indexing and conversion jobs, in the database (bulk upload spec 2026-11-14, phase 1; table
doc_index_jobs, migration 027).

A row is one document waiting for, or doing, one job. There is one row per document, so an indexing job and a
conversion of the same document can't both exist, on this worker or another. Each worker's runners
(doc_pipeline.run_queue_forever for the index lane, run_convert_forever for conversions) claim one row at a time.

The rules a claim follows are the rebuild queue's (rebuild_queue.py):
- `FOR UPDATE SKIP LOCKED`: two workers never get one document, and neither waits on the other.
- `claimed_by` is the worker; `claimed_at` its heartbeat. A running row not heard from in CLAIM_LAPSE is taken
  over (its worker died). A worker's late finish after that is ignored: it no longer owns the row.
- `attempts` counts claims. The runner gives a job up once it passes MAX_ATTEMPTS: a file that kills its worker
  would otherwise kill one every CLAIM_LAPSE, for ever.

Unlike a rebuild, a job ends once: the runner deletes the row whether the work succeeded or failed (the failure
is on the document, and the person who asked is told). Nothing is retried by itself except a dead worker's job.

Every function runs in the caller's transaction and doesn't commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Jsonb

from .rebuild_queue import CLAIM_LAPSE

INDEX_JOBS = ("pipeline", "legacy_swap")   # the index lane: one document at a time per worker, with rebuilds
CONVERT_JOBS = ("convert",)                # the convert lane: one Docling run at a time per worker
JOBS = INDEX_JOBS + CONVERT_JOBS
MAX_ATTEMPTS = 3                           # claims; the next one gives the job up


@dataclass(frozen=True)
class Claim:
    doc_id: str
    job: str
    requested_by: str | None   # who hears how it ended; None: nobody
    attempts: int              # this claim included
    options: dict | None       # a conversion's options


def _check(job: str) -> None:
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}")


async def enqueue(conn, doc_id: str, *, job: str = "pipeline", requested_by: str | None = None,
                  options: dict | None = None) -> bool:
    """Queue `job` for `doc_id`. Idempotent: a document that already has a job keeps it as it is, with its first
    requester. True when it wrote."""
    _check(job)
    cur = await conn.execute(
        "INSERT INTO doc_index_jobs (doc_id, job, requested_by, options) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (doc_id) DO NOTHING RETURNING doc_id",
        (doc_id, job, requested_by, Jsonb(options) if options is not None else None))
    return await cur.fetchone() is not None


async def enqueue_convert(conn, doc_id: str, *, requested_by: str | None, options: dict) -> bool:
    """Queue a conversion. A job still waiting becomes this conversion, in its place in the queue (a conversion
    ends by indexing, and a queued conversion takes the newer options). False when the document's job is already
    running: nothing is changed, and the caller refuses."""
    cur = await conn.execute(
        "INSERT INTO doc_index_jobs (doc_id, job, requested_by, options) VALUES (%s, 'convert', %s, %s) "
        "ON CONFLICT (doc_id) DO UPDATE SET job = 'convert', requested_by = EXCLUDED.requested_by, "
        "options = EXCLUDED.options, attempts = 0 WHERE doc_index_jobs.state = 'queued' RETURNING doc_id",
        (doc_id, requested_by, Jsonb(options)))
    return await cur.fetchone() is not None


async def claim_next(conn, worker: str, jobs: tuple[str, ...]) -> Claim | None:
    """Claim the oldest claimable row among `jobs` for `worker`: queued, or running with a lapsed claim."""
    cur = await conn.execute(
        "UPDATE doc_index_jobs SET state = 'running', claimed_by = %s, claimed_at = now(), attempts = attempts + 1 "
        "WHERE doc_id = (SELECT doc_id FROM doc_index_jobs WHERE job = ANY(%s) "
        f"  AND (state = 'queued' OR (state = 'running' AND claimed_at < now() - interval '{CLAIM_LAPSE}')) "
        "  ORDER BY queued_at, doc_id LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING doc_id, job, requested_by::text, attempts, options", (worker, list(jobs)))
    row = await cur.fetchone()
    return Claim(*row) if row else None


async def heartbeat(conn, doc_id: str, worker: str) -> None:
    await conn.execute("UPDATE doc_index_jobs SET claimed_at = now() WHERE doc_id = %s AND claimed_by = %s "
                       "AND state = 'running'", (doc_id, worker))


async def finish(conn, doc_id: str, worker: str) -> bool:
    """The job is over, however it went: the row goes, if `worker` still owns it. False when it doesn't (the claim
    lapsed and another worker took it, or the document was deleted)."""
    cur = await conn.execute("DELETE FROM doc_index_jobs WHERE doc_id = %s AND claimed_by = %s AND state = 'running' "
                             "RETURNING doc_id", (doc_id, worker))
    return await cur.fetchone() is not None


LIVE = f"state = 'running' AND claimed_at >= now() - interval '{CLAIM_LAPSE}'"   # a worker is on it now


async def live_claim(conn, doc_id: str) -> bool:
    cur = await conn.execute(f"SELECT 1 FROM doc_index_jobs WHERE doc_id = %s AND {LIVE}", (doc_id,))
    return await cur.fetchone() is not None


async def positions(conn) -> dict[str, int]:
    """doc_id -> how many jobs are ahead of it. A running job is 0. A waiting job counts what runs and what waits
    before it in its lane; in the index lane the queued rebuilds too, since the runner takes those first."""
    cur = await conn.execute(
        "SELECT doc_id, job = ANY(%s) AS indexing, state = 'running' AS running, "
        "       row_number() OVER (PARTITION BY job = ANY(%s) ORDER BY (state = 'running') DESC, queued_at, doc_id) - 1, "
        "       (SELECT count(*) FROM doc_rebuilds WHERE state = 'queued') "
        "FROM doc_index_jobs", (list(INDEX_JOBS), list(INDEX_JOBS)))
    return {doc_id: 0 if running else ahead + (rebuilds if indexing else 0)
            for doc_id, indexing, running, ahead, rebuilds in await cur.fetchall()}


async def queue_counts(conn) -> dict[str, int]:
    cur = await conn.execute("SELECT state, count(*) FROM doc_index_jobs GROUP BY state")
    counts = {"queued": 0, "running": 0}
    counts.update({state: n for state, n in await cur.fetchall()})
    return counts
