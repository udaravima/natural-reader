"""Background document jobs (A1 spec §4): extract text from verified bytes,
embed chunks, and the one-time re-extraction of legacy browser-indexed content.

One asyncio lock per doc serializes every job that touches a doc's chunks.
The lock is per process: two server workers may duplicate work on the same
new hash, and converge on UNIQUE(doc_id, text_hash) — wasted work, not wrong
data (spec §4)."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
from pathlib import Path
from typing import Any

from ..db import get_pool, is_ready
from . import doc_storage, docling_convert, extract, rebuild_queue
from .doc_outlines import BOOKMARKS, HEADINGS, NO_OUTLINE, Outline, save_outline
from .doc_search import ReadableDoc
from . import embedding_state
from .embeddings import EMBEDDING_DIM, Embedder, embed_documents, profile_model, profile_of

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


def chunk_key(c: extract.Chunk) -> str:
    """doc_chunks.text_hash, the UNIQUE(doc_id, text_hash) key. Position and
    page are part of it: identical split parts (a letterhead opening every
    page) must stay separate rows on their own pages (v2.3 Task E review)."""
    return text_hash(f"{c.ord}\x00{c.page}\x00{c.text}")


async def replace_chunks(conn, doc_id, chunks, *, embeddings=None, model=None) -> None:
    """Swap a doc's whole chunk set in ONE transaction: readers see the old set
    until commit and never a half-swapped index (spec §4, legacy content)."""
    rows = [
        (doc_id, c.ord, c.page, c.chunk_type, c.text, chunk_key(c),
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

    It marks the document indexed only if it is still 'indexing': a content
    op that ran meanwhile (delete-markdown wipes the chunks and sets 'stored'
    for a fresh extraction) has put it somewhere else, and stamping 'indexed'
    over that would leave it reading Indexed with no passages.
    """
    lock = doc_lock(doc_id)
    async with lock:
        if not is_ready():
            logger.warning("Skipping index job for %s — DB not ready", doc_id)
            return
        pool = get_pool()
        try:
            async with pool.connection() as conn:
                # Read once per job, so the recorded model and profile are the
                # ones the vectors were made with.
                e = await embedding_state.current(conn)
                embed_model, profile = e.model, profile_of(e)
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
                            embedding_profile = %s, error_message = NULL, updated_at = now()
                        WHERE doc_id = %s AND state = 'indexing'
                        """,
                        (embed_model, EMBEDDING_DIM, profile, doc_id),
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
                while True:
                    vectors = await embed_documents(texts, e)
                    async with pool.connection() as conn, conn.transaction():
                        # The library may have switched models while this batch was embedded (ledger ruling
                        # P2): the state row, held FOR SHARE, says which model `embedding` is for now.
                        now = await embedding_state.locked_current(conn)
                        if embedding_state.vector_identity(now) != embedding_state.vector_identity(e):
                            e = now
                            embed_model, profile = e.model, profile_of(e)
                            continue   # embed this batch again, with the new model
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
                    break

            # Searchable with what did embed; but the profile is recorded only
            # when every chunk embedded, so a document with gaps stays stale
            # and its next use rebuilds it (v2.3 final review minor).
            async with pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE documents
                    SET state = 'indexed', embedding_model = %s, embedding_dim = %s,
                        embedding_profile = COALESCE(%s, embedding_profile), error_message = NULL,
                        updated_at = now()
                    WHERE doc_id = %s AND state = 'indexing'
                    """,
                    (embed_model, EMBEDDING_DIM, None if skipped_count else profile, doc_id),
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


async def _page_source(conn, doc_id: str) -> tuple[list[extract.Chunk], Outline]:
    """A converted document's chunks (one per non-empty Markdown page, split)
    and its outline: the headings of those pages."""
    cur = await conn.execute("SELECT page, markdown FROM doc_pages WHERE doc_id = %s ORDER BY page", (doc_id,))
    pages = [p for p in ((page, docling_convert.strip_figure_refs(md or "").strip())
                         for page, md in await cur.fetchall()) if p[1]]
    chunks = extract.split_chunks([extract.Chunk(i, page, "page-md", text)
                                   for i, (page, text) in enumerate(pages)])
    entries = extract.outline_from_markdown_pages(pages)
    return chunks, Outline(entries, extract.opening_line(chunks), HEADINGS if entries else NO_OUTLINE)


def _native_outline(result: extract.Extraction, file_type: str) -> Outline:
    """The outline of a file extracted natively: a Markdown file's headings,
    a PDF's bookmarks, each only when there are any; otherwise just the
    opening line, with source 'none'."""
    if result.outline and file_type == "markdown":
        source = HEADINGS
    elif result.outline and file_type == "pdf":
        source = BOOKMARKS
    else:
        source = NO_OUTLINE
    return Outline(result.outline if source != NO_OUTLINE else (), result.opening, source)


async def chunks_from_pages(doc_id: str) -> int:
    """
    After conversion: replace the chunks for `doc_id` with ones made from
    `doc_pages` (one per page, split when long), and save the outline from the
    same pages. Embeddings will be (re)generated by the indexing job. Returns
    the number of chunk rows.
    """
    async with get_pool().connection() as conn:
        chunks, outline = await _page_source(conn, doc_id)
        async with conn.transaction():
            await replace_chunks(conn, doc_id, chunks)
            await save_outline(conn, doc_id, outline)
    return len(chunks)


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
        bytes_path = doc_storage.resolve_stored(bytes_path)
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
                    # Legacy bytes (pre-A1) were never verified against doc_id:
                    # a resume/re-index of one would otherwise native-extract
                    # from unverified content and mark it server-derived.
                    actual_hash = await asyncio.to_thread(doc_storage.sha256_file, Path(bytes_path))
                    if actual_hash != doc_id:
                        logger.warning("Stored bytes don't match doc %s (hash=%s)", doc_id, actual_hash)
                        await _fail(doc_id, "Stored file doesn't match this document — "
                                             "upload the file again.")
                        return
                    async with get_pool().connection() as conn:
                        await conn.execute(
                            "UPDATE documents SET state = 'extracting', error_message = NULL, "
                            "updated_at = now() WHERE doc_id = %s", (doc_id,))
                    result = await asyncio.to_thread(extract.extract_file, Path(bytes_path), file_type)
                    if not result.chunks:
                        # e.g. a scanned PDF with no text layer: 'indexed' with
                        # nothing to search would look like success. Docling's
                        # OCR can still read it.
                        await _fail(doc_id, "No text found — try Convert (OCR).")
                        return
                    async with get_pool().connection() as conn:
                        async with conn.transaction():
                            await replace_chunks(conn, doc_id, extract.split_chunks(result.chunks))
                            await save_outline(conn, doc_id, _native_outline(result, file_type))
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
    async with doc_lock(doc_id):
        if not is_ready():
            return
        async with get_pool().connection() as conn:
            e = await embedding_state.current(conn)
            cur = await conn.execute(
                "SELECT state, extracted_by, bytes_path, file_type FROM documents "
                "WHERE doc_id = %s", (doc_id,))
            row = await cur.fetchone()
        if not row or row[0] != "indexed" or row[1] != "client" or not row[2]:
            return
        bytes_path = doc_storage.resolve_stored(row[2])
        # Legacy doc ids ARE the sha256 of their bytes (spec §4), so benign
        # legacy bytes always pass this. A mismatch means the bytes were
        # swapped in unverified (e.g. via the old, unverified /pdf route).
        actual_hash = await asyncio.to_thread(doc_storage.sha256_file, Path(bytes_path))
        if actual_hash != doc_id:
            logger.warning("Stored bytes don't match doc %s; skipping legacy swap (hash=%s)",
                            doc_id, actual_hash)
            return
        try:
            result = await asyncio.to_thread(extract.extract_file, Path(bytes_path), row[3])
            if not result.chunks:
                logger.warning("Legacy re-extraction of %s produced no chunks; "
                                "keeping the old index", doc_id)
                return
            chunks = extract.split_chunks(result.chunks)
            texts = [c.text for c in chunks]
            vectors = []
            for i in range(0, len(texts), EMBED_BATCH):
                vectors += await embed_documents(texts[i:i + EMBED_BATCH], e)
            if any(v is None for v in vectors):
                raise RuntimeError("embedding service skipped some chunks")
        except Exception as e:
            logger.error("Legacy re-extraction failed for %s; keeping the old index: %s", doc_id, e)
            return
        async with get_pool().connection() as conn:
            async with conn.transaction():
                if embedding_state.vector_identity(await embedding_state.locked_current(conn)) != \
                        embedding_state.vector_identity(e):   # switched models meanwhile (ruling P2)
                    logger.warning("Legacy re-extraction of %s dropped: the embedding model changed meanwhile; "
                                   "the next upload tries again", doc_id)
                    return
                await replace_chunks(conn, doc_id, chunks, embeddings=vectors, model=e.model)
                await save_outline(conn, doc_id, _native_outline(result, row[3]))
                await conn.execute("DELETE FROM doc_pages WHERE doc_id = %s", (doc_id,))
                await conn.execute(
                    "UPDATE documents SET extracted_by = 'server', state = 'indexed', "
                    "page_count = %s, embedding_model = %s, embedding_dim = %s, embedding_profile = %s, "
                    "conversion_state = NULL, conversion_options = NULL, conversion_error = NULL, "
                    "converted_at = NULL, error_message = NULL, updated_at = now() "
                    "WHERE doc_id = %s",
                    (result.page_count, e.model, EMBEDDING_DIM, profile_of(e), doc_id))
        doc_storage.remove_figures(doc_id)   # their Markdown just went
        logger.warning("Legacy content %s re-extracted from verified bytes", doc_id)


# ---------- rebuilding under the current embedding profile (v2.3 Task E; the queue: phase 2b) ----------

QUEUE_POLL_S = 5.0          # an idle runner looks again this often (an enqueue in this process wakes it at once)
HEARTBEAT_S = 60.0          # a running rebuild refreshes its claim this often (rebuild_queue.CLAIM_LAPSE: 10 min)
ENQUEUE_WAIT_S = 0.5        # ensure_current's own connection: with the pool full, skip rather than wait
_wake = asyncio.Event()


async def ensure_current(conn, doc: ReadableDoc | None, e: Embedder | None = None) -> ReadableDoc | None:
    """On first use of an indexed document made under another embedding
    profile (before v2.3: whole-page chunks, no prefixes; or another model),
    queue one background rebuild. Meanwhile its old chunks keep answering,
    unless its vectors came from another model: those can't be compared with
    a query's, so it reads as "reindexing" and isn't searched until the swap.
    `e` is the current embedder when the caller already read it (a project's
    documents); else it is read on `conn`."""
    if doc is None or doc.state != "indexed":
        return doc
    e = e or await embedding_state.current(conn)
    profile = profile_of(e)
    if doc.embedding_profile == profile:
        return doc
    await _enqueue_soon(doc.doc_id, profile)
    model = doc.embedding_model or profile_model(doc.embedding_profile)
    if model and model.removesuffix(":latest") != e.model.removesuffix(":latest"):
        return dataclasses.replace(doc, state="reindexing")
    return doc


async def _enqueue_soon(doc_id: str, profile: str) -> None:
    """Enqueue on a connection of its own, committed at once: a row lock held in the reading request's
    transaction would make every other reader of this document wait for that request to end (the lesson of
    auth/touch.py). A full pool skips it; the next use enqueues."""
    try:
        async with get_pool().connection(timeout=ENQUEUE_WAIT_S) as conn:
            wrote = await rebuild_queue.enqueue(conn, doc_id, profile)
    except Exception as err:  # noqa: BLE001 — a read must never fail because its rebuild couldn't be queued
        logger.info("Rebuild of %s not queued this time: %s", doc_id, type(err).__name__)
        return
    if wrote:
        _wake.set()


async def run_queue_once(worker: str = rebuild_queue.WORKER_ID) -> str | None:
    """Claim one queued rebuild, run it, record how it went. Returns the doc_id, or None when nothing was
    claimable."""
    async with get_pool().connection() as conn:
        doc_id = await rebuild_queue.claim_next(conn, worker)
    if doc_id is None:
        return None

    async def beat():
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            async with get_pool().connection() as conn:
                await rebuild_queue.heartbeat(conn, doc_id, worker)

    beating = asyncio.create_task(beat())
    try:
        error = await run_rebuild(doc_id)
    finally:
        beating.cancel()
    async with get_pool().connection() as conn:
        if error:
            await rebuild_queue.fail(conn, doc_id, worker, error)
        else:
            await rebuild_queue.finish(conn, doc_id, worker)
    return doc_id


async def run_queue_forever(worker: str = rebuild_queue.WORKER_ID) -> None:
    """One per worker process, started with the app: drain the queue one document at a time (the embedding
    model is shared), then wait for an enqueue here or QUEUE_POLL_S for one elsewhere."""
    while True:
        try:
            if is_ready() and await run_queue_once(worker):
                continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a background loop: log and keep going
            logger.warning("Rebuild queue step failed", exc_info=True)
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), QUEUE_POLL_S)
        except TimeoutError:
            pass


def _embedding_part(profile: str | None) -> str | None:
    """The part of an embedding profile that decides what a text's vector is:
    model | document prefix | query prefix, without the chunker version."""
    return "|".join(profile.split("|")[:3]) if profile else None


async def _reusable_vectors(conn, doc_id: str, old_profile: str | None, e: Embedder) -> dict[str, Any]:
    """The vectors a rebuild can keep, by chunk text. A vector depends only on
    its text, the model and the document prefix, so one made under the same
    model and prefixes is as good as a fresh one, and embedding is the slow
    part of a rebuild on a CPU. Empty when the profile differs in any of
    those (or is unknown): such a vector is not comparable with a query's.
    The profile is the document's as a whole; a row is also checked on its own
    (embedding_model), since a document whose re-index partly failed can hold
    vectors from two models under one profile."""
    if _embedding_part(old_profile) != _embedding_part(profile_of(e)):
        return {}
    cur = await conn.execute(
        "SELECT text, embedding FROM doc_chunks "
        "WHERE doc_id = %s AND embedding IS NOT NULL AND embedding_model = %s",
        (doc_id, e.model))
    return dict(await cur.fetchall())


async def _rebuild_source(conn, doc_id: str, row) -> tuple[list[extract.Chunk], Outline]:
    """The text to re-chunk, with its outline: converted pages; else the
    verified stored file; else (legacy content with no usable bytes) the chunks
    it has, which have no outline."""
    file_type, bytes_path, conversion_state, extracted_by = row
    bytes_path = doc_storage.resolve_stored(bytes_path)
    if conversion_state == "converted":
        return await _page_source(conn, doc_id)
    if extracted_by == "server" and bytes_path and Path(bytes_path).is_file():
        if await asyncio.to_thread(doc_storage.sha256_file, Path(bytes_path)) == doc_id:
            result = await asyncio.to_thread(extract.extract_file, Path(bytes_path), file_type)
            return extract.split_chunks(result.chunks), _native_outline(result, file_type)
    cur = await conn.execute("SELECT ord, page, chunk_type, text FROM doc_chunks WHERE doc_id = %s "
                             "ORDER BY ord", (doc_id,))
    chunks = extract.split_chunks([extract.Chunk(*r) for r in await cur.fetchall()])
    return chunks, Outline((), extract.opening_line(chunks), NO_OUTLINE)


async def run_rebuild(doc_id: str) -> str | None:
    """Re-chunk and re-embed an indexed document under the current profile.
    The new set is built and embedded first, then swapped in with one
    transaction: state stays 'indexed' and the old set answers until then
    (plan Review focus 6). Any failure keeps the old index. Only text with no
    vector yet is embedded (see _reusable_vectors): after a chunker upgrade
    that is the new chunks, not the document.

    Returns why it failed, or None when it is done or had nothing to do. The
    queue runner (run_queue_once) runs one at a time per worker, so a rebuild
    never holds its document's lock while waiting for another."""
    async with doc_lock(doc_id):
        if not is_ready():
            return "the database isn't ready"
        try:
            async with get_pool().connection() as conn:
                e = await embedding_state.current(conn)
                profile, embed_model = profile_of(e), e.model
                cur = await conn.execute(
                    "SELECT state, embedding_profile, file_type, bytes_path, conversion_state, extracted_by "
                    "FROM documents WHERE doc_id = %s", (doc_id,))
                row = await cur.fetchone()
                if not row or row[0] != "indexed" or row[1] == profile:
                    return None
                chunks, outline = await _rebuild_source(conn, doc_id, row[2:])
                reusable = await _reusable_vectors(conn, doc_id, row[1], e)
            if not chunks:
                logger.warning("Rebuild of %s found no text; keeping the old index", doc_id)
                return "no text was found to rebuild from"
            # Each distinct text without a vector, once.
            missing = list(dict.fromkeys(c.text for c in chunks if c.text not in reusable))
            embedded: dict[str, Any] = {}
            for i in range(0, len(missing), EMBED_BATCH):
                batch = missing[i:i + EMBED_BATCH]
                embedded.update(zip(batch, await embed_documents(batch, e)))
            if any(v is None for v in embedded.values()):
                raise RuntimeError("the embedding service skipped some chunks")
            vectors = [reusable[c.text] if c.text in reusable else embedded[c.text] for c in chunks]
            async with get_pool().connection() as conn:
                async with conn.transaction():
                    # The state row first, as the switch takes it, then the document row: one order, no deadlock.
                    if embedding_state.vector_identity(await embedding_state.locked_current(conn)) != \
                            embedding_state.vector_identity(e):   # switched models meanwhile (ruling P2)
                        logger.info("The embedding model changed during the rebuild of %s; its next use queues "
                                    "it again", doc_id)
                        return None
                    # Routes that reset a document (POST /index, a conversion
                    # revert, a delete) don't take doc_lock: swap only if it is
                    # still the indexed document this rebuild started from.
                    cur = await conn.execute("SELECT state, embedding_profile FROM documents "
                                             "WHERE doc_id = %s FOR UPDATE", (doc_id,))
                    now = await cur.fetchone()
                    if not now or now[0] != "indexed" or now[1] != row[1]:
                        logger.info("Doc %s changed during its rebuild; dropped the rebuilt set", doc_id)
                        return None
                    await replace_chunks(conn, doc_id, chunks, embeddings=vectors, model=embed_model)
                    await save_outline(conn, doc_id, outline)
                    await conn.execute(
                        "UPDATE documents SET embedding_profile = %s, embedding_model = %s, "
                        "embedding_dim = %s, updated_at = now() WHERE doc_id = %s AND state = 'indexed'",
                        (profile, embed_model, EMBEDDING_DIM, doc_id))
            logger.info("Rebuilt doc %s: %d chunks under the current embedding profile (%d texts embedded, "
                        "%d vectors kept)", doc_id, len(chunks), len(missing), len(reusable))
            return None
        except Exception as err:  # noqa: BLE001 — a background job: log, keep the old index
            logger.error("Rebuild of %s failed; keeping the old index: %s", doc_id, type(err).__name__)
            logger.debug("Rebuild failure detail", exc_info=True)
            return str(err) or type(err).__name__
