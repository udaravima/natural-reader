"""The Docling conversion job (PDF to per-page Markdown, then indexing), moved out of routers/docs.py so the queue's
convert lane (doc_pipeline.run_convert_forever) can run it without importing a router. The route enqueues; this runs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from psycopg.types.json import Jsonb

from ..db import get_pool, is_ready
from . import doc_pipeline, doc_storage, docling_convert

logger = logging.getLogger("server.routers.docs")   # the name its log lines have always had


class ConversionProgress:
    """Stores docling's latest progress report for the Convert button
    (documents.conversion_progress), which polls GET /v1/docs/{id}: in the
    database, not in memory, because with WORKERS > 1 the poll can reach
    another process. Docling reports from its worker thread, so each report
    is handed to the event loop; only the latest is kept, and one write is in
    flight at a time, so a fast run of pages never queues up writes. A write
    lands only while the document is still converting: one that loses the
    race with the job's final UPDATE changes nothing."""

    def __init__(self, loop: asyncio.AbstractEventLoop, doc_id: str):
        self._loop = loop
        self._doc_id = doc_id
        self._latest: dict[str, Any] | None = None
        self._writer: asyncio.Task | None = None

    def __call__(self, progress: dict[str, Any]) -> None:   # docling's thread
        self._loop.call_soon_threadsafe(self._set, progress)

    def _set(self, progress: dict[str, Any]) -> None:
        self._latest = progress
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._latest is not None:
            progress, self._latest = self._latest, None
            try:
                async with get_pool().connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET conversion_progress = %s "
                        "WHERE doc_id = %s AND conversion_state = 'converting'",
                        (Jsonb(progress), self._doc_id))
            except Exception:   # the button just shows less; the conversion goes on
                logger.debug("Could not store conversion progress for %s", self._doc_id, exc_info=True)

    async def close(self) -> None:
        """The conversion is over: drop what hasn't been written, and wait out a write in flight."""
        self._latest = None
        if self._writer is not None:
            await asyncio.gather(self._writer, return_exceptions=True)


async def run_convert_job(doc_id: str, options: dict[str, Any]) -> None:
    """
    Background task: convert the retained PDF for `doc_id` to per-page Markdown,
    store in `doc_pages`, then chain into the existing embedding pipeline so the
    doc lands at `state='indexed'` when finished.
    """
    lock = doc_pipeline.doc_lock(doc_id)
    async with lock:
        if not is_ready():
            logger.warning("Skipping convert job for %s — DB not ready", doc_id)
            return
        pool = get_pool()
        progress = ConversionProgress(asyncio.get_running_loop(), doc_id)
        try:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT bytes_path FROM documents WHERE doc_id = %s",
                    (doc_id,),
                )
                row = await cur.fetchone()
            if not row or not row[0]:
                raise RuntimeError("No retained PDF for this document")

            try:
                converted = await docling_convert.convert_pdf_to_markdown_pages(
                    doc_storage.resolve_stored(row[0]), options, on_progress=progress)
            finally:
                await progress.close()
            pages = converted.pages
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
            await asyncio.to_thread(doc_storage.replace_figures, doc_id, converted.pictures)

            # 'converted' and 'indexing' land together: a poller that sees
            # the conversion done must not also see the OLD 'indexed' state
            # and report success before the converted chunks are embedded.
            async with pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE documents
                    SET conversion_state = 'converted',
                        conversion_error = NULL,
                        conversion_progress = NULL,
                        converted_at = now(),
                        state = 'indexing',
                        error_message = NULL,
                        updated_at = now()
                    WHERE doc_id = %s
                    """,
                    (doc_id,),
                )

            # Auto-chain into indexing: seed chunks from MD, then embed.
            inserted = await doc_pipeline.chunks_from_pages(doc_id)
            logger.info("Convert job: seeded %d chunks for %s", inserted, doc_id)
        except Exception as e:
            logger.exception("Convert job failed for %s", doc_id)
            try:
                async with get_pool().connection() as conn:
                    await conn.execute(
                        """
                        UPDATE documents
                        SET conversion_state = 'conversion_failed',
                            conversion_error = %s,
                            conversion_progress = NULL,
                            -- set to 'indexing' with 'converted' above; don't
                            -- leave it claiming an embed job that never runs
                            state = CASE WHEN state = 'indexing' THEN 'failed' ELSE state END,
                            error_message = CASE WHEN state = 'indexing' THEN %s
                                                 ELSE error_message END,
                            updated_at = now()
                        WHERE doc_id = %s
                        """,
                        (str(e)[:500], f"Conversion failed: {e}"[:500], doc_id),
                    )
            except Exception:
                logger.exception("Could not record conversion failure for %s", doc_id)
            return

    # Run the embedding job outside the conversion lock: run_embed takes the
    # same lock itself.
    await doc_pipeline.run_embed(doc_id)
