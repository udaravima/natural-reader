"""Background document jobs (A1 spec §4): extract text from verified bytes,
embed chunks, and the one-time re-extraction of legacy browser-indexed content.

One asyncio lock per doc serializes every job that touches a doc's chunks.
The lock is per process: two server workers may duplicate work on the same
new hash, and converge on UNIQUE(doc_id, text_hash) — wasted work, not wrong
data (spec §4)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from ..db import get_pool, is_ready
from . import extract, model_router
from .embeddings import EMBEDDING_DIM, embed_batch

logger = logging.getLogger(__name__)

EMBED_BATCH = 16

# Per-doc lock: embedding, extraction and conversion jobs all touch the same
# doc_chunks rows, so they share it.
_doc_job_locks: dict[str, asyncio.Lock] = {}


def doc_lock(doc_id: str) -> asyncio.Lock:
    lock = _doc_job_locks.get(doc_id)
    if lock is None:
        lock = asyncio.Lock()
        _doc_job_locks[doc_id] = lock
    return lock


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def replace_chunks(conn, doc_id, chunks, *, embeddings=None, model=None) -> None:
    """Swap a doc's whole chunk set in ONE transaction: readers see the old set
    until commit and never a half-swapped index (spec §4, legacy content)."""
    rows = [
        (doc_id, c.ord, c.page, c.chunk_type, c.text, text_hash(c.text),
         embeddings[i] if embeddings else None, model if embeddings else None)
        for i, c in enumerate(chunks)
    ]
    async with conn.transaction():
        await conn.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, "
                "embedding, embedding_model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (doc_id, text_hash) DO UPDATE SET ord = EXCLUDED.ord, "
                "page = EXCLUDED.page, chunk_type = EXCLUDED.chunk_type, "
                "embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model",
                rows)


async def run_embed(doc_id: str) -> None:
    """
    Background task: pulls all chunks for `doc_id` with NULL embeddings,
    embeds them in batches, and writes the vectors back. Updates the
    document state to `indexed` on success or `failed` on error.

    Held under a per-doc lock so concurrent /index calls coalesce instead of
    duplicating work.
    """
    # Read once per job so the recorded metadata matches what embed_one
    # actually used (both source from model_router).
    embed_model = model_router.get_config().embed_model
    lock = doc_lock(doc_id)
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
                        (embed_model, EMBEDDING_DIM, doc_id),
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
                                (vec, embed_model, chunk_id),
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
                    (embed_model, EMBEDDING_DIM, doc_id),
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


async def chunks_from_pages(doc_id: str) -> int:
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
                        (doc_id, ord_, page, "page-md", text, text_hash(text)),
                    )
                    inserted += 1
    return inserted


async def _fail(doc_id: str, message: str) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE documents SET state = 'failed', error_message = %s, updated_at = now() "
            "WHERE doc_id = %s", (message[:500], doc_id))


async def run_pipeline(doc_id: str) -> None:
    """stored | extracting | failed → extract (native, or from converted pages)
    → extracted → embed → indexed. `extracted` skips straight to embedding.
    Every failure lands in state='failed' with a readable error_message."""
    async with doc_lock(doc_id):
        if not is_ready():
            logger.warning("Skipping pipeline for %s — DB not ready", doc_id)
            return
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "SELECT state, file_type, bytes_path, conversion_state FROM documents "
                "WHERE doc_id = %s", (doc_id,))
            row = await cur.fetchone()
        if row is None:
            return
        state, file_type, bytes_path, conversion_state = row
        if state in ("stored", "extracting", "failed"):
            # Only native extraction from verified bytes makes content
            # server-derived; re-seeding from converted pages leaves
            # extracted_by as it was (legacy conversions stay 'client').
            native = conversion_state != "converted"
            try:
                if not native:
                    await chunks_from_pages(doc_id)
                    page_count = None
                else:
                    if not bytes_path or not Path(bytes_path).is_file():
                        raise RuntimeError("No stored file — upload the file again.")
                    async with get_pool().connection() as conn:
                        await conn.execute(
                            "UPDATE documents SET state = 'extracting', error_message = NULL, "
                            "updated_at = now() WHERE doc_id = %s", (doc_id,))
                    result = await asyncio.to_thread(extract.extract_file, Path(bytes_path), file_type)
                    async with get_pool().connection() as conn:
                        await replace_chunks(conn, doc_id, result.chunks)
                    page_count = result.page_count
                    logger.debug("Extracted %d chunks from %d pages for %s",
                                 len(result.chunks), page_count, doc_id)
                async with get_pool().connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET state = 'extracted', "
                        "extracted_by = CASE WHEN %s THEN 'server' ELSE extracted_by END, "
                        "page_count = COALESCE(%s, page_count), updated_at = now() "
                        "WHERE doc_id = %s", (native, page_count, doc_id))
            except Exception as e:
                logger.error("Extraction failed for %s: %s", doc_id, e)
                await _fail(doc_id, f"Could not extract text: {e}")
                return
        elif state != "extracted":
            return  # indexing / indexed: nothing to do
        async with get_pool().connection() as conn:
            await conn.execute(
                "UPDATE documents SET state = 'indexing', updated_at = now() WHERE doc_id = %s",
                (doc_id,))
    await run_embed(doc_id)  # takes the same lock itself


async def run_legacy_swap(doc_id: str) -> None:
    """Content indexed from browser chunks before A1 is re-extracted ONCE from
    the first verified upload (spec §4). New chunks are extracted and embedded
    first; one transaction then swaps them in. Any failure keeps the old
    index untouched and leaves extracted_by='client' so a later upload retries.
    A conversion made from the old, unverified bytes is discarded with it."""
    embed_model = model_router.get_config().embed_model
    async with doc_lock(doc_id):
        if not is_ready():
            return
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "SELECT state, extracted_by, bytes_path, file_type FROM documents "
                "WHERE doc_id = %s", (doc_id,))
            row = await cur.fetchone()
        if not row or row[0] != "indexed" or row[1] != "client" or not row[2]:
            return
        try:
            result = await asyncio.to_thread(extract.extract_file, Path(row[2]), row[3])
            texts = [c.text for c in result.chunks]
            vectors = []
            for i in range(0, len(texts), EMBED_BATCH):
                vectors += await embed_batch(texts[i:i + EMBED_BATCH])
            if any(v is None for v in vectors):
                raise RuntimeError("embedding service skipped some chunks")
        except Exception as e:
            logger.error("Legacy re-extraction failed for %s; keeping the old index: %s", doc_id, e)
            return
        async with get_pool().connection() as conn:
            async with conn.transaction():
                await replace_chunks(conn, doc_id, result.chunks, embeddings=vectors, model=embed_model)
                await conn.execute("DELETE FROM doc_pages WHERE doc_id = %s", (doc_id,))
                await conn.execute(
                    "UPDATE documents SET extracted_by = 'server', state = 'indexed', "
                    "page_count = %s, embedding_model = %s, embedding_dim = %s, "
                    "conversion_state = NULL, conversion_options = NULL, conversion_error = NULL, "
                    "converted_at = NULL, error_message = NULL, updated_at = now() "
                    "WHERE doc_id = %s",
                    (result.page_count, embed_model, EMBEDDING_DIM, doc_id))
        logger.warning("Legacy content %s re-extracted from verified bytes", doc_id)
