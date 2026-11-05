"""The per-document rebuild queue, in the database (admin console spec §4.2.4; table doc_rebuilds, migration 025).

A document indexed under an older profile (another chunker, older prefixes, vectors from a model that is no
longer current) is rebuilt on its first use: ensure_current enqueues it, and each worker's runner
(doc_pipeline.run_queue_forever) claims one row at a time.

The rules a claim follows:
- `FOR UPDATE SKIP LOCKED`: two workers never get one document, and neither waits on the other.
- `claimed_by` is the worker; `claimed_at` its heartbeat. A running row not heard from in CLAIM_LAPSE is
  taken over (its worker died). A worker's late finish or failure after that is ignored: it no longer owns it.
- A failed row waits RETRY_AFTER before it may be claimed again, so a document that can't be rebuilt isn't
  re-embedded on every turn.
- Enqueueing a newer profile resets the row to queued, even while it runs: the running worker's finish then
  leaves it, and it is built again for the newer profile.

Every function runs in the caller's transaction and doesn't commit.
"""
from __future__ import annotations

import os
import secrets
import socket

CLAIM_LAPSE = "10 minutes"
RETRY_AFTER = "15 minutes"

# This process, as a claimer: unique across hosts and restarts.
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


async def enqueue(conn, doc_id: str, profile: str) -> bool:
    """Queue `doc_id` for a rebuild under `profile`. Idempotent: a row already asking for this profile is left
    as it is (queued, running, or failed and waiting). True when it wrote."""
    cur = await conn.execute("SELECT 1 FROM doc_rebuilds WHERE doc_id = %s AND profile = %s", (doc_id, profile))
    if await cur.fetchone():
        return False
    await conn.execute(
        "INSERT INTO doc_rebuilds (doc_id, profile) VALUES (%s, %s) "
        "ON CONFLICT (doc_id) DO UPDATE SET profile = EXCLUDED.profile, state = 'queued', attempts = 0, "
        "error = NULL, claimed_by = NULL, claimed_at = NULL, retry_after = NULL, updated_at = now() "
        "WHERE doc_rebuilds.profile <> EXCLUDED.profile", (doc_id, profile))
    return True


async def claim_next(conn, worker: str) -> str | None:
    """Claim the oldest claimable row for `worker`: queued, running with a lapsed claim, or failed and past its
    wait. Returns its doc_id, or None."""
    cur = await conn.execute(
        "UPDATE doc_rebuilds SET state = 'running', claimed_by = %s, claimed_at = now(), "
        "attempts = attempts + 1, updated_at = now() "
        "WHERE doc_id = (SELECT doc_id FROM doc_rebuilds "
        f"  WHERE state = 'queued' OR (state = 'running' AND claimed_at < now() - interval '{CLAIM_LAPSE}') "
        "     OR (state = 'failed' AND retry_after <= now()) "
        "  ORDER BY updated_at, doc_id LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING doc_id", (worker,))
    row = await cur.fetchone()
    return row[0] if row else None


async def heartbeat(conn, doc_id: str, worker: str) -> None:
    await conn.execute("UPDATE doc_rebuilds SET claimed_at = now() WHERE doc_id = %s AND claimed_by = %s "
                       "AND state = 'running'", (doc_id, worker))


async def finish(conn, doc_id: str, worker: str) -> None:
    """The rebuild is done (or had nothing to do): the row goes, if `worker` still owns it."""
    await conn.execute("DELETE FROM doc_rebuilds WHERE doc_id = %s AND claimed_by = %s AND state = 'running'",
                       (doc_id, worker))


async def fail(conn, doc_id: str, worker: str, error: str) -> None:
    await conn.execute(
        "UPDATE doc_rebuilds SET state = 'failed', error = %s, claimed_by = NULL, claimed_at = NULL, "
        f"retry_after = now() + interval '{RETRY_AFTER}', updated_at = now() "
        "WHERE doc_id = %s AND claimed_by = %s AND state = 'running'", (error[:500], doc_id, worker))


async def queue_counts(conn) -> dict[str, int]:
    cur = await conn.execute("SELECT state, count(*) FROM doc_rebuilds GROUP BY state")
    counts = {"queued": 0, "running": 0, "failed": 0}
    counts.update({state: n for state, n in await cur.fetchall()})
    return counts
