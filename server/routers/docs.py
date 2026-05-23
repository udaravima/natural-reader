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
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..db import get_pool, is_ready
from ..services import docling_convert
from ..services.embeddings import EMBEDDING_DIM, EMBEDDING_MODEL, embed_batch, embed_one


# Filesystem location for retained PDF bytes. Overridable via env so the
# docker-compose mount can park them on a named volume in production.
PDF_STORAGE_DIR = Path(os.environ.get("PDF_STORAGE_DIR", "./data/pdfs")).resolve()
PDF_UPLOAD_MAX_MB = int(os.environ.get("PDF_UPLOAD_MAX_MB", "50"))

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


class ConvertOptionsIn(BaseModel):
    preset: str = Field(default="standard", pattern="^(fast|standard|accurate)$")
    ocr: bool = False
    tables: bool = True
    images: str = Field(default="drop", pattern="^(drop|embed|describe)$")
    # Inclusive 1-based [start, end]. None = whole document.
    page_range: list[int] | None = None


# Per-doc lock prevents concurrent /index *or* /convert calls for the same doc
# from stomping each other. Shared between embedding and conversion jobs since
# they touch the same doc_chunks rows.
_doc_job_locks: dict[str, asyncio.Lock] = {}
# Back-compat alias for the original name used in PR 4. Kept so any external
# call sites (none in-repo, but easy to grep for) still resolve.
_index_locks = _doc_job_locks


def _get_doc_lock(doc_id: str) -> asyncio.Lock:
    lock = _doc_job_locks.get(doc_id)
    if lock is None:
        lock = asyncio.Lock()
        _doc_job_locks[doc_id] = lock
    return lock


# Retained for backwards reference to the PR 4 helper name.
_get_index_lock = _get_doc_lock


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
                   d.conversion_state, d.conversion_options, d.conversion_error,
                   d.converted_at, d.pdf_path,
                   COALESCE(c.cnt, 0) AS chunk_count,
                   COALESCE(c.embedded, 0) AS embedded_count,
                   COALESCE(p.page_cnt, 0) AS converted_page_count
            FROM documents d
            LEFT JOIN (
                SELECT doc_id,
                       COUNT(*) AS cnt,
                       COUNT(embedding) AS embedded
                FROM doc_chunks
                GROUP BY doc_id
            ) c ON c.doc_id = d.doc_id
            LEFT JOIN (
                SELECT doc_id, COUNT(*) AS page_cnt FROM doc_pages GROUP BY doc_id
            ) p ON p.doc_id = d.doc_id
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
    if rec.get("converted_at") is not None:
        rec["converted_at"] = _epoch_ms(rec["converted_at"])
    # Don't leak the absolute filesystem path to the client.
    rec["has_pdf"] = bool(rec.pop("pdf_path", None))
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
        await cur.execute(
            "DELETE FROM documents WHERE doc_id = %s RETURNING pdf_path",
            (doc_id,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    _doc_job_locks.pop(doc_id, None)
    # Best-effort cleanup of the retained PDF.
    pdf_path = row[0]
    if pdf_path:
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove PDF for %s at %s: %s", doc_id, pdf_path, e)
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
    lock = _get_doc_lock(doc_id)
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
            embedded_count = 0
            skipped_count = 0
            for i in range(0, len(rows), BATCH):
                slice_ = rows[i : i + BATCH]
                texts = [r[1] for r in slice_]
                vectors = await embed_batch(texts)
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        for (chunk_id, _text), vec in zip(slice_, vectors):
                            if vec is None:
                                skipped_count += 1
                                continue
                            await cur.execute(
                                """
                                UPDATE doc_chunks
                                SET embedding = %s, embedding_model = %s
                                WHERE id = %s
                                """,
                                (vec, EMBEDDING_MODEL, chunk_id),
                            )
                            embedded_count += 1

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
            logger.info(
                "Indexed %d chunks for doc %s (%d embedded, %d skipped)",
                len(rows), doc_id, embedded_count, skipped_count,
            )
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


# ---------- Docling conversion (PDF → Markdown) ----------

def _pdf_storage_path(doc_id: str) -> Path:
    PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return PDF_STORAGE_DIR / f"{doc_id}.pdf"


@router.post("/{doc_id}/pdf")
async def upload_pdf_bytes(doc_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """
    Persist the raw PDF bytes for `doc_id` to the backend filesystem so the
    convert job (and any future reconversion) can read them without another
    upload from the browser. Re-upload overwrites in place.
    """
    _ensure_ready()
    pool = get_pool()
    # Confirm the row exists first — otherwise we'd happily park bytes for a
    # doc that was never registered.
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM documents WHERE doc_id = %s", (doc_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Register the document first")

    max_bytes = PDF_UPLOAD_MAX_MB * 1024 * 1024
    path = _pdf_storage_path(doc_id)
    written = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"PDF exceeds {PDF_UPLOAD_MAX_MB} MB limit",
                    )
                out.write(chunk)
    finally:
        await file.close()

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE documents SET pdf_path = %s, updated_at = now() WHERE doc_id = %s",
            (str(path), doc_id),
        )
    return {"ok": True, "doc_id": doc_id, "size_bytes": written}


@router.delete("/{doc_id}/pdf")
async def delete_pdf_bytes(doc_id: str) -> dict[str, Any]:
    """Remove retained PDF bytes for `doc_id`. Keeps converted markdown / chunks."""
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT pdf_path FROM documents WHERE doc_id = %s", (doc_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        pdf_path = row[0]
        await cur.execute(
            "UPDATE documents SET pdf_path = NULL, updated_at = now() WHERE doc_id = %s",
            (doc_id,),
        )
    if pdf_path:
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove PDF for %s: %s", doc_id, e)
    return {"ok": True, "doc_id": doc_id}


async def _chunks_from_pages(doc_id: str) -> int:
    """
    After conversion: wipe existing chunks for `doc_id` and seed new ones from
    `doc_pages` (one chunk per page). Embeddings will be (re)generated by the
    indexing job. Returns the number of inserted chunk rows.
    """
    pool = get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
                await cur.execute(
                    "SELECT page, markdown FROM doc_pages WHERE doc_id = %s ORDER BY page",
                    (doc_id,),
                )
                pages = await cur.fetchall()
                inserted = 0
                for ord_, (page, markdown) in enumerate(pages):
                    text = (markdown or "").strip()
                    if not text:
                        continue
                    await cur.execute(
                        """
                        INSERT INTO doc_chunks
                            (doc_id, ord, page, chunk_type, text, text_hash)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (doc_id, text_hash) DO UPDATE SET
                            ord = EXCLUDED.ord, page = EXCLUDED.page,
                            chunk_type = EXCLUDED.chunk_type
                        """,
                        (doc_id, ord_, page, "page-md", text, _text_hash(text)),
                    )
                    inserted += 1
    return inserted


async def _run_convert_job(doc_id: str, options: dict[str, Any]) -> None:
    """
    Background task: convert the retained PDF for `doc_id` to per-page Markdown,
    store in `doc_pages`, then chain into the existing embedding pipeline so the
    doc lands at `state='indexed'` when finished.
    """
    lock = _get_doc_lock(doc_id)
    async with lock:
        if not is_ready():
            logger.warning("Skipping convert job for %s — DB not ready", doc_id)
            return
        pool = get_pool()
        try:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT pdf_path FROM documents WHERE doc_id = %s",
                    (doc_id,),
                )
                row = await cur.fetchone()
            if not row or not row[0]:
                raise RuntimeError("No retained PDF for this document")
            pdf_path = Path(row[0])

            pages = await docling_convert.convert_pdf_to_markdown_pages(pdf_path, options)
            if not pages:
                raise RuntimeError("Conversion produced no pages")

            # Persist pages (delete-then-insert; reconversion replaces).
            async with pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        await cur.execute("DELETE FROM doc_pages WHERE doc_id = %s", (doc_id,))
                        for page_no, md in pages:
                            await cur.execute(
                                "INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s, %s, %s)",
                                (doc_id, page_no, md),
                            )

            async with pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE documents
                    SET conversion_state = 'converted',
                        conversion_error = NULL,
                        converted_at = now(),
                        updated_at = now()
                    WHERE doc_id = %s
                    """,
                    (doc_id,),
                )

            # Auto-chain into indexing: seed chunks from MD, then embed.
            inserted = await _chunks_from_pages(doc_id)
            logger.info("Convert job: seeded %d chunks for %s", inserted, doc_id)
            if inserted:
                async with pool.connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET state = 'indexing', error_message = NULL, updated_at = now() WHERE doc_id = %s",
                        (doc_id,),
                    )
        except Exception as e:
            logger.exception("Convert job failed for %s", doc_id)
            try:
                async with get_pool().connection() as conn:
                    await conn.execute(
                        """
                        UPDATE documents
                        SET conversion_state = 'conversion_failed',
                            conversion_error = %s,
                            updated_at = now()
                        WHERE doc_id = %s
                        """,
                        (str(e)[:500], doc_id),
                    )
            except Exception:
                logger.exception("Could not record conversion failure for %s", doc_id)
            return

    # Run the embedding job outside the conversion lock — _run_index_job
    # acquires the same lock itself.
    await _run_index_job(doc_id)


@router.post("/{doc_id}/convert", status_code=202)
async def start_convert_job(
    doc_id: str,
    options: ConvertOptionsIn,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """
    Kick off a docling conversion in the background. Requires that
    POST /v1/docs/{doc_id}/pdf has stored the bytes first. Returns 202; poll
    GET /v1/docs/{doc_id} to watch `conversion_state` and then `state`.
    """
    _ensure_ready()
    if not docling_convert.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Docling is disabled on this server (set DOCLING_ENABLED=true).",
        )

    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT pdf_path FROM documents WHERE doc_id = %s",
            (doc_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        if not row[0]:
            raise HTTPException(
                status_code=409,
                detail="Upload the PDF bytes first via POST /v1/docs/{doc_id}/pdf",
            )
        await conn.execute(
            """
            UPDATE documents
            SET conversion_state = 'converting',
                conversion_options = %s,
                conversion_error = NULL,
                updated_at = now()
            WHERE doc_id = %s
            """,
            (Jsonb(options.model_dump()), doc_id),
        )

    background.add_task(_run_convert_job, doc_id, options.model_dump())
    return {"ok": True, "doc_id": doc_id, "conversion_state": "converting"}


@router.get("/{doc_id}/markdown")
async def get_document_markdown(doc_id: str, page: int | None = None) -> Response:
    """
    Return the docling-converted Markdown for this document. Without `page`
    the whole document is returned (pages joined with form-feed markers); with
    `page=N` only that page's Markdown is returned.
    """
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        if page is not None:
            await cur.execute(
                "SELECT markdown FROM doc_pages WHERE doc_id = %s AND page = %s",
                (doc_id, page),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Page not found")
            return Response(content=row[0] or "", media_type="text/markdown")

        await cur.execute(
            "SELECT page, markdown FROM doc_pages WHERE doc_id = %s ORDER BY page",
            (doc_id,),
        )
        rows = await cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="No converted markdown for this document")
    # Join with a labelled separator so the MD reader can split client-side
    # without losing page boundaries. Single newlines around the marker keep
    # the result valid Markdown.
    body = "\n\n".join(f"<!-- page {p} -->\n\n{md}" for (p, md) in rows)
    return Response(content=body, media_type="text/markdown")


@router.delete("/{doc_id}/markdown")
async def delete_document_markdown(doc_id: str) -> dict[str, Any]:
    """
    Wipe a document's converted markdown and the chunks/embeddings derived
    from it. The document row itself stays (so re-conversion is a single
    click), as does the retained PDF (delete via DELETE /pdf if needed).

    Sets `conversion_state=NULL` so the toolbar shows the inviting "Convert"
    label again. The doc-level `state` flips back to `registered` because the
    chunks are gone — the user can either re-run convert or fall back to the
    native client-side `Index` flow.
    """
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM documents WHERE doc_id = %s", (doc_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Document not found")
        async with conn.transaction():
            await conn.execute("DELETE FROM doc_pages WHERE doc_id = %s", (doc_id,))
            # The chunks were seeded from doc_pages, so they're stale now.
            # Re-indexing without reconverting would just re-create them empty.
            await conn.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
            await conn.execute(
                """
                UPDATE documents
                SET conversion_state = NULL,
                    conversion_error = NULL,
                    converted_at = NULL,
                    state = 'registered',
                    embedding_model = NULL,
                    embedding_dim = NULL,
                    error_message = NULL,
                    updated_at = now()
                WHERE doc_id = %s
                """,
                (doc_id,),
            )
    return {"ok": True, "doc_id": doc_id}
