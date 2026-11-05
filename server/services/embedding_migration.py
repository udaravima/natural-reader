"""Re-embed the library with a new model in the background, then switch to it in one step (admin console spec
§4.2.4, "build, then switch").

While a migration runs, a second column `doc_chunks.embedding_next vector(768)`, with its own HNSW index, is
filled with the target model; queries keep using `embedding` and the current model, so every document stays
searchable. When every vectored chunk has a new vector, one transaction drops the old column, renames the new
one (and its index) into its place, and makes the target current. The admin can cancel before that, which
drops the column.

The rules that keep two models out of one column:
- `start`, `switch` and `cancel` take the embedding_state row FOR UPDATE before any table lock; every writer of
  vectors takes it FOR SHARE in its write transaction and checks the embedder it used is still the one that
  column is for (ledger ruling P2). The same order everywhere, so they can't deadlock.
- The switch holds `doc_chunks` in SHARE ROW EXCLUSIVE mode while it checks for unfilled rows: a chunk indexed
  meanwhile, with the current model, sends it back to filling (Review Focus 1).
- One filler at a time across workers (ruling R5): it claims the state row with a heartbeat, and a claim not
  renewed in CLAIM_LAPSE_S is taken over (Focus 2). A new target restarts the fill from nothing (Focus 3).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ..db import get_pool, is_ready
from . import console_settings, embedding_state
from .embedding_state import from_json, read_state, to_json, vector_identity, write_state
from .embeddings import EMBEDDING_DIM, EMBEDDING_TIMEOUT_S, Embedder, EmbeddingError, embed_texts, probe, profile_of

logger = logging.getLogger(__name__)

FILL_BATCH = 32          # chunks per fill step: one embedding request
# A filler not heard from in this long has died and another takes over: longer than one request may take.
CLAIM_LAPSE_S = max(120.0, EMBEDDING_TIMEOUT_S + 60)
IDLE_S = 5.0             # an idle loop looks again this often
ERROR_S = 30.0           # after a failed step (the target unreachable), wait this long
# A target that didn't answer (not pulled yet, provider down) is probed again after this long; one that answered
# with the wrong size is refused until the target changes.
UNREACHABLE_RETRY_S = 300.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lapsed(claimed_at: str | None) -> bool:
    if not claimed_at:
        return True
    try:
        return _now() - datetime.fromisoformat(claimed_at) > timedelta(seconds=CLAIM_LAPSE_S)
    except ValueError:
        return True


def _due(at: str | None) -> bool:
    """Whether a recorded retry time has come. None: never (a final refusal)."""
    if not at:
        return False
    try:
        return _now() >= datetime.fromisoformat(at)
    except ValueError:
        return True


def _cleared(state: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    """The state with no migration running, and `changes` applied."""
    return {**state, "next": None, "filled": 0, "total": 0, "claimed_by": None, "claimed_at": None,
            "error": None, "error_for": None, "error_retry_at": None, **changes}


async def _locked(conn, env: Mapping[str, str]) -> dict[str, Any]:
    state = await read_state(conn, lock="update")
    if state is None:
        await embedding_state.init(conn, env)
        state = await read_state(conn, lock="update")
    return state


async def _drop_next(conn) -> None:
    await conn.execute("ALTER TABLE doc_chunks DROP COLUMN IF EXISTS embedding_next")   # its index goes with it


async def _has_next(conn) -> bool:
    cur = await conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'doc_chunks' "
                             "AND column_name = 'embedding_next'")
    return await cur.fetchone() is not None


async def start(conn, target: Embedder, *, by: str | None = None, env: Mapping[str, str] = os.environ) -> str:
    """Make `target` the library's model. Its `dimensions` must already be decided (the probe's).
    - The same vectors as current (same model and prefixes, maybe another provider): switch at once, with no
      re-embed; any running migration is dropped. Returns "switched".
    - Another model: drop any earlier `embedding_next`, add an empty one with its index, and record the target
      as `next`; the filler does the rest. Returns "started". Starting the target already being built changes
      nothing."""
    async with conn.transaction():
        state = await _locked(conn, env)
        current = from_json(state["current"], env)
        key = vector_identity(target)
        if key == vector_identity(current):
            await _drop_next(conn)
            # Keep current's own model spelling: the documents' profiles name it, and they stay valid.
            await write_state(conn, _cleared(state, current={**state["current"], "provider": target.provider.name}),
                              by=by)
            return "switched"
        nxt = state.get("next")
        if nxt and vector_identity(from_json(nxt, env)) == key and await _has_next(conn):
            await write_state(conn, {**state, "next": to_json(target), "error": None, "error_for": None,
                                     "error_retry_at": None}, by=by)
            return "started"
        await _drop_next(conn)
        await conn.execute(f"ALTER TABLE doc_chunks ADD COLUMN embedding_next vector({EMBEDDING_DIM})")
        await conn.execute("CREATE INDEX doc_chunks_embedding_next_idx ON doc_chunks "
                           "USING hnsw (embedding_next vector_cosine_ops)")
        cur = await conn.execute("SELECT count(*) FROM doc_chunks WHERE embedding IS NOT NULL")
        total = (await cur.fetchone())[0]
        await write_state(conn, _cleared(state, next=to_json(target), total=total), by=by)
    logger.info("Re-embedding %d passages for %s on %s", total, target.model, target.provider.name)
    return "started"


async def fill_step(worker: str, *, env: Mapping[str, str] = os.environ) -> str:
    """Embed up to FILL_BATCH chunks into `embedding_next` with the target. Returns:
    "idle" (no migration, or it changed while this batch was embedded: the batch is dropped),
    "claimed-elsewhere" (another worker is filling), "filled", "ready" (nothing left: switch), or
    "error" (the target failed; recorded in the state for the Overview)."""
    async with get_pool().connection() as conn:
        async with conn.transaction():
            state = await read_state(conn, lock="update")
            if not state or not state.get("next"):
                return "idle"
            if state.get("claimed_by") not in (None, worker) and not _lapsed(state.get("claimed_at")):
                return "claimed-elsewhere"
            nxt = state["next"]
            await write_state(conn, {**state, "claimed_by": worker, "claimed_at": _now().isoformat()})
        cur = await conn.execute("SELECT id, text_hash, text FROM doc_chunks "
                                 "WHERE embedding IS NOT NULL AND embedding_next IS NULL ORDER BY id LIMIT %s",
                                 (FILL_BATCH,))
        rows = await cur.fetchall()
    if not rows:
        return "ready"
    target = from_json(nxt, env)
    try:
        vectors = await embed_texts(target, [target.document_prefix + text for _, _, text in rows])
    except EmbeddingError as err:
        async with get_pool().connection() as conn:
            async with conn.transaction():
                state = await read_state(conn, lock="update")
                if state and state.get("next") == nxt:
                    await write_state(conn, {**state, "error": str(err)})
        logger.warning("Re-embedding paused: %s", err)
        return "error"
    async with get_pool().connection() as conn:
        async with conn.transaction():
            state = await read_state(conn, lock="update")
            if not state or state.get("next") != nxt:
                return "idle"   # cancelled, or another target chosen while this batch was embedded
            written = 0
            for (chunk_id, text_hash, _text), vec in zip(rows, vectors):
                cur = await conn.execute(
                    "UPDATE doc_chunks SET embedding_next = %s WHERE id = %s AND text_hash = %s "
                    "AND embedding IS NOT NULL AND embedding_next IS NULL", (vec, chunk_id, text_hash))
                written += cur.rowcount
            await write_state(conn, {**state, "filled": state.get("filled", 0) + written,
                                     "claimed_at": _now().isoformat(), "error": None})
    return "filled"


async def switch(conn, *, env: Mapping[str, str] = os.environ) -> bool:
    """When every vectored chunk has a new vector, make the target current in one transaction. False (and
    nothing changed) while any is unfilled, or with no migration."""
    async with conn.transaction():
        state = await read_state(conn, lock="update")
        if not state or not state.get("next"):
            return False
        await conn.execute("LOCK TABLE doc_chunks IN SHARE ROW EXCLUSIVE MODE")
        cur = await conn.execute("SELECT 1 FROM doc_chunks WHERE embedding IS NOT NULL AND embedding_next IS NULL "
                                 "LIMIT 1")
        if await cur.fetchone():
            return False
        old, new = from_json(state["current"], env), from_json(state["next"], env)
        await conn.execute("ALTER TABLE doc_chunks DROP COLUMN embedding")      # and doc_chunks_embedding_idx
        await conn.execute("ALTER TABLE doc_chunks RENAME COLUMN embedding_next TO embedding")
        await conn.execute("ALTER INDEX doc_chunks_embedding_next_idx RENAME TO doc_chunks_embedding_idx")
        await conn.execute("UPDATE doc_chunks SET embedding_model = %s WHERE embedding IS NOT NULL", (new.model,))
        # A document that was current is current again under the new model. One under an older profile (another
        # chunker) keeps it, so its next use still rebuilds it; its vectors are the new model's either way.
        await conn.execute(
            "UPDATE documents SET embedding_model = %s, embedding_profile = CASE WHEN embedding_profile = %s "
            "THEN %s ELSE embedding_profile END, updated_at = now() WHERE state = 'indexed'",
            (new.model, profile_of(old), profile_of(new)))
        await write_state(conn, _cleared(state, current=state["next"], filled=state.get("total", 0),
                                         total=state.get("total", 0)))
    logger.info("Switched the library to %s", new.model)
    return True


async def cancel(conn, *, by: str | None = None, env: Mapping[str, str] = os.environ) -> None:
    """Stop a migration before its switch: drop the new column; the current model stays. The target goes back to
    current (ledger ruling P4), or the next tick would start the same migration again: when current is what the
    environment says, the console's choice is removed; otherwise it is set to current."""
    async with conn.transaction():
        state = await read_state(conn, lock="update")
        if not state:
            return
        await _drop_next(conn)
        await write_state(conn, _cleared(state), by=by)
        current = from_json(state["current"], env)
        env_target = embedding_state.env_target(env)
        if vector_identity(env_target) == vector_identity(current) and env_target.provider.name == current.provider.name:
            changes = {"provider": None, "model": None}
        else:
            changes = {"provider": current.provider.name, "model": current.model}
        await console_settings.save(conn, embedding_state.SECTION, changes, updated_by=by, env=env)


async def tick(worker: str, *, env: Mapping[str, str] = os.environ) -> str:
    """One step of the loop. With a migration running: fill, and switch when ready. Without: when the target
    differs from current (a console choice saved on another worker, or an env change at startup, ruling R3),
    probe it and start; a refusal is recorded once, not probed again until the target changes. Returns what it
    did: "idle", "filled", "switched", "started", "refused", "claimed-elsewhere" or "error"."""
    async with get_pool().connection() as conn:
        state = await read_state(conn)
        if state is None:
            await embedding_state.init(conn, env)
            state = await read_state(conn)
        target = await embedding_state.load_target(conn, env)
    if state.get("next"):
        step = await fill_step(worker, env=env)
        if step != "ready":
            return step
        async with get_pool().connection() as conn:
            return "switched" if await switch(conn, env=env) else "filled"
    current = from_json(state["current"], env)
    key = vector_identity(target)
    if key == vector_identity(current):
        if target.provider.name == current.provider.name:
            return "idle"
        async with get_pool().connection() as conn:
            return await start(conn, target, env=env)
    if state.get("error_for") == key and not _due(state.get("error_retry_at")):
        return "idle"
    verdict = await probe(target.provider, target.model)
    async with get_pool().connection() as conn:
        if not verdict.ok:
            # A model that answered with the wrong size stays refused; one that didn't answer is tried again.
            retry = None if verdict.native is not None else \
                (_now() + timedelta(seconds=UNREACHABLE_RETRY_S)).isoformat()
            async with conn.transaction():
                state = await _locked(conn, env)
                await write_state(conn, {**state, "error": verdict.reason, "error_for": key, "error_retry_at": retry})
            logger.warning("Not re-embedding with %s: %s", target.model, verdict.reason)
            return "refused"
        return await start(conn, replace(target, dimensions=verdict.dimensions), env=env)


async def run_forever(worker: str) -> None:
    """One per worker process, started with the app. Only one fills at a time (the claim); the others look in
    every IDLE_S."""
    while True:
        what = "idle"
        try:
            if is_ready():
                what = await tick(worker)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a background loop: log and keep going
            logger.warning("Embedding migration step failed", exc_info=True)
            what = "error"
        if what in ("filled", "started", "switched"):
            await asyncio.sleep(0)
            continue
        await asyncio.sleep(ERROR_S if what == "error" else IDLE_S)
