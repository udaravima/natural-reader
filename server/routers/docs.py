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

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_pool, is_ready

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
    return {"ok": True, "doc_id": doc_id}
