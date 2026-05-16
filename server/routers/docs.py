"""
Document registration + chunk ingest endpoints.

PR 3 scope: take chunks the frontend already extracted from the loaded doc and
persist them in Postgres. No embedding yet — `embedding` stays NULL until PR 4
wires up the Ollama embedding pipeline. The state machine is:

    registered  → POST /v1/docs created the row
    chunks_uploaded → at least one chunk batch has landed
    indexing    → (PR 4) background embed job running
    indexed     → all chunks have embeddings
    failed      → (PR 4) embedding job errored

Chunk inserts are idempotent on (doc_id, text_hash) so re-running the Index
button is safe — the existing rows are upserted in place.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..db import get_pool, is_ready
from ..services.embeddings import EMBEDDING_DIM, EMBEDDING_MODEL, embed_batch, embed_one

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/docs", tags=["docs"])


# ---------- request / response models ----------

class DocRegisterIn(BaseModel):
    doc_id: str = Field(min_length=64, max_length=64)
    file_name: str
    file_type: str = Field(pattern="^(pdf|text|markdown)$")
    size_bytes: int = Field(ge=0)
    page_count: int | None = Field(default=None, ge=0)


class ChunkIn(BaseModel):
    ord: int = Field(ge=0)
    page: int | None = Field(default=None, ge=0)
    chunk_type: str | None = None
    text: str


class ChunksUploadIn(BaseModel):
    chunks: list[ChunkIn]


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    k: int = Field(default=5, ge=1, le=20)


# Per-doc lock prevents two concurrent /index calls for the same doc from
# stomping each other. New entries are created on demand.
_index_locks: dict[str, asyncio.Lock] = {}


def _get_index_lock(doc_id: str) -> asyncio.Lock:
    lock = _index_locks.get(doc_id)
    if lock is None:
        lock = asyncio.Lock()
        _index_locks[doc_id] = lock
    return lock


# ---------- helpers ----------

def _ensure_ready() -> None:
    if not is_ready():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — doc indexing is offline",
        )


def _epoch_ms(ts) -> int:
    return int(ts.timestamp() * 1000)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _fetch_doc_status(conn, doc_id: str) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT d.doc_id, d.file_name, d.file_type, d.size_bytes, d.page_count,
                   d.state, d.embedding_model, d.embedding_dim, d.error_message,
                   d.created_at, d.updated_at,
                   COALESCE(c.cnt, 0) AS chunk_count,
                   COALESCE(c.embedded, 0) AS embedded_count
            FROM documents d
            LEFT JOIN (
                SELECT doc_id,
                       COUNT(*) AS cnt,
                       COUNT(embedding) AS embedded
                FROM doc_chunks
                GROUP BY doc_id
            ) c ON c.doc_id = d.doc_id
            WHERE d.doc_id = %s
            """,
            (doc_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
    rec = dict(zip(cols, row))
    rec["created_at"] = _epoch_ms(rec["created_at"])
    rec["updated_at"] = _epoch_ms(rec["updated_at"])
    return rec


# ---------- routes ----------

@router.post("")
async def register_document(payload: DocRegisterIn) -> dict[str, Any]:
    """
    Upsert a document row. Idempotent on `doc_id` — re-registering the same
    sha256 hash returns the existing row unchanged (apart from `updated_at`).
    """
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO documents (doc_id, file_name, file_type, size_bytes, page_count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (doc_id) DO UPDATE SET
                file_name  = EXCLUDED.file_name,
                file_type  = EXCLUDED.file_type,
                size_bytes = EXCLUDED.size_bytes,
                page_count = EXCLUDED.page_count,
                updated_at = now()
            """,
            (
                payload.doc_id,
                payload.file_name,
                payload.file_type,
                payload.size_bytes,
                payload.page_count,
            ),
        )
        status = await _fetch_doc_status(conn, payload.doc_id)
    return status


@router.get("/{doc_id}")
async def get_document(doc_id: str) -> dict[str, Any]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        status = await _fetch_doc_status(conn, doc_id)
    if not status:
        raise HTTPException(status_code=404, detail="Document not found")
    return status


@router.post("/{doc_id}/chunks")
async def upload_chunks(doc_id: str, payload: ChunksUploadIn) -> dict[str, Any]:
    """
    Bulk insert/upsert chunks. The frontend should call this in batches (~50)
    rather than one giant payload — keeps individual requests bounded and lets
    the UI show incremental progress.

    Chunks are upserted on (doc_id, text_hash): re-indexing the same doc reuses
    existing rows (preserving any embeddings from PR 4) rather than duplicating.
    The doc's state advances to `chunks_uploaded` on the first successful batch
    (stays at `indexed` / `indexing` if it had already progressed past that).
    """
    _ensure_ready()
    if not payload.chunks:
        return {"ok": True, "inserted": 0, "doc_id": doc_id}

    pool = get_pool()
    async with pool.connection() as conn:
        # Verify the document exists — chunks for an unregistered doc_id would
        # FK-violate, but a clean 404 is friendlier than a 500.
        async with conn.cursor() as cur:
            await cur.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Register the document first")
            current_state = row[0]

        async with conn.transaction():
            async with conn.cursor() as cur:
                for chunk in payload.chunks:
                    text = chunk.text.strip()
                    if not text:
                        continue
                    th = _text_hash(text)
                    await cur.execute(
                        """
                        INSERT INTO doc_chunks
                            (doc_id, ord, page, chunk_type, text, text_hash)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (doc_id, text_hash) DO UPDATE SET
                            ord        = EXCLUDED.ord,
                            page       = EXCLUDED.page,
                            chunk_type = EXCLUDED.chunk_type
                        """,
                        (doc_id, chunk.ord, chunk.page, chunk.chunk_type, text, th),
                    )

            # Only nudge state forward from the pre-embedding stages — don't
            # rewind a doc that's already `indexed` back to `chunks_uploaded`.
            if current_state in (None, "registered"):
                await conn.execute(
                    "UPDATE documents SET state = 'chunks_uploaded', updated_at = now() WHERE doc_id = %s",
                    (doc_id,),
                )

        status = await _fetch_doc_status(conn, doc_id)

    return {"ok": True, "inserted": len(payload.chunks), "doc_id": doc_id, "status": status}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, Any]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("DELETE FROM documents WHERE doc_id = %s RETURNING doc_id", (doc_id,))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    _index_locks.pop(doc_id, None)
    return {"ok": True, "doc_id": doc_id}


# ---------- embeddings + retrieval ----------

async def _run_index_job(doc_id: str) -> None:
    """
    Background task: pulls all chunks for `doc_id` with NULL embeddings,
    embeds them in batches, and writes the vectors back. Updates the
    document state to `indexed` on success or `failed` on error.

    Held under a per-doc lock so concurrent /index calls coalesce instead of
    duplicating work.
    """
    lock = _get_index_lock(doc_id)
    async with lock:
        if not is_ready():
            logger.warning("Skipping index job for %s — DB not ready", doc_id)
            return
        pool = get_pool()
        try:
            async with pool.connection() as conn:
                # Pull only the chunks we still need to embed. The model name
                # is captured per row so a swap (which requires re-creating
                # the column) doesn't silently mix dims.
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT id, text FROM doc_chunks WHERE doc_id = %s AND embedding IS NULL ORDER BY ord",
                        (doc_id,),
                    )
                    rows = await cur.fetchall()

            if not rows:
                # Nothing left to embed — mark indexed and bail.
                async with pool.connection() as conn:
                    await conn.execute(
                        """
                        UPDATE documents
                        SET state = 'indexed', embedding_model = %s, embedding_dim = %s,
                            error_message = NULL, updated_at = now()
                        WHERE doc_id = %s
                        """,
                        (EMBEDDING_MODEL, EMBEDDING_DIM, doc_id),
                    )
                return

            # Embed in moderate batches; the semaphore in embed_batch caps
            # parallelism per call. Persist after each batch so a partial
            # failure halfway through still saves progress.
            BATCH = 16
            for i in range(0, len(rows), BATCH):
                slice_ = rows[i : i + BATCH]
                texts = [r[1] for r in slice_]
                vectors = await embed_batch(texts)
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        for (chunk_id, _text), vec in zip(slice_, vectors):
                            await cur.execute(
                                """
                                UPDATE doc_chunks
                                SET embedding = %s, embedding_model = %s
                                WHERE id = %s
                                """,
                                (vec, EMBEDDING_MODEL, chunk_id),
                            )

            async with pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE documents
                    SET state = 'indexed', embedding_model = %s, embedding_dim = %s,
                        error_message = NULL, updated_at = now()
                    WHERE doc_id = %s
                    """,
                    (EMBEDDING_MODEL, EMBEDDING_DIM, doc_id),
                )
            logger.info("Indexed %d chunks for doc %s", len(rows), doc_id)
        except Exception as e:
            logger.exception("Index job failed for %s", doc_id)
            try:
                pool = get_pool()
                async with pool.connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET state = 'failed', error_message = %s, updated_at = now() WHERE doc_id = %s",
                        (str(e)[:500], doc_id),
                    )
            except Exception:
                logger.exception("Could not record failure state for %s", doc_id)


@router.post("/{doc_id}/index", status_code=202)
async def start_index_job(doc_id: str, background: BackgroundTasks) -> dict[str, Any]:
    """
    Kick off a background embedding job for `doc_id`. Returns 202 immediately;
    poll `GET /v1/docs/{doc_id}` for progress (`embedded_count` / `chunk_count`).

    Safe to call repeatedly — the per-doc lock serializes runs and the
    embedding query only picks up chunks with NULL embeddings, so a re-run
    after a partial failure only does the leftover work.
    """
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT state FROM documents WHERE doc_id = %s",
            (doc_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        # Mark as indexing — the bg task will flip to indexed/failed when done.
        await conn.execute(
            "UPDATE documents SET state = 'indexing', error_message = NULL, updated_at = now() WHERE doc_id = %s",
            (doc_id,),
        )

    background.add_task(_run_index_job, doc_id)
    return {"ok": True, "doc_id": doc_id, "state": "indexing"}


@router.post("/{doc_id}/search")
async def search_document(doc_id: str, req: SearchIn) -> dict[str, Any]:
    """
    Semantic search over `doc_id`'s embedded chunks. Returns top-k chunks
    by cosine similarity (higher `score` = closer).
    """
    _ensure_ready()
    pool = get_pool()
    # Verify the doc exists first so we can give a clean 404 instead of an
    # empty result set the caller has to interpret.
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT state FROM documents WHERE doc_id = %s",
            (doc_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")

    try:
        qvec = await embed_one(req.query)
    except Exception as e:
        logger.exception("Embedding failed for search query")
        raise HTTPException(status_code=502, detail=f"Embedding service error: {e}") from e

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, page, chunk_type, text,
                   1 - (embedding <=> %s::vector) AS score
            FROM doc_chunks
            WHERE doc_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (qvec, doc_id, qvec, req.k),
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]

    results = [dict(zip(cols, r)) for r in rows]
    for r in results:
        # Cap text length in the response — full chunk text can be huge and
        # the chat preamble already truncates. Keep payloads bounded.
        if r.get("text") and len(r["text"]) > 4000:
            r["text"] = r["text"][:4000] + " [truncated]"
    return {"doc_id": doc_id, "results": results}
