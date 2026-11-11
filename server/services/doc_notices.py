"""Document notices for the person who asked (spec docs/superpowers/specs/2026-11-06-notifications-design.md).

The routes that start an indexing or a conversion schedule `run_and_notify(job, doc_id, ..., requested_by, via)`
instead of the bare job. It runs the job, then reads the document's committed state and sends one notice (plan
ruling P1): the jobs mark a document failed in many places, and reading where they left it can't miss one, while a
change that didn't commit is never reported. A job started by nobody (the rebuild queue, a startup swap) isn't
wrapped, and sends nothing to anyone in person.

The notice names the document as the requester knows it (their library entry's name, else the content's own), copied
in, because it may be renamed or gone by the time it's read. Passages the embedding model shortened during the job
are an admin's concern (choosing the model), so they go to every admin, once per job."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from ..db import get_pool
from . import embedding_state, notifications
from .embeddings import counting_shortened

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 500   # as the rebuild queue caps its own; an error can be a whole traceback


async def run_and_notify(job: Callable[..., Awaitable[None]], doc_id: str, *job_args,
                         requested_by: str, via: Literal["index", "convert"]) -> None:
    with counting_shortened() as tally:
        try:
            await job(doc_id, *job_args)
        except Exception:
            logger.exception("Document job failed for %s", doc_id)   # a background task: logged, as before
    try:
        async with get_pool().connection() as conn:
            async with conn.transaction():
                await _notify(conn, doc_id, requested_by, via, tally.texts)
    except Exception:
        logger.warning("Document notices for %s not sent", doc_id, exc_info=True)


async def _notify(conn, doc_id: str, requested_by: str, via: str, shortened: int) -> None:
    cur = await conn.execute(
        "SELECT d.state, d.error_message, d.conversion_state, d.conversion_error, "
        "COALESCE(le.file_name, d.file_name), d.file_name FROM documents d "
        "LEFT JOIN library_entries le ON le.doc_id = d.doc_id AND le.user_id = %s WHERE d.doc_id = %s",
        (requested_by, doc_id))
    row = await cur.fetchone()
    if row is None:
        return   # deleted meanwhile: nothing to point at
    state, error, conversion_state, conversion_error, name, canonical = row
    target = notifications.doc_target(doc_id, name)
    common = {"doc_id": doc_id, "target": target}
    if via == "convert" and conversion_state == "conversion_failed":
        await notifications.send(conn, [requested_by], "doc.convert_failed", title=f"Couldn't convert {name}.",
                                 body=(conversion_error or "")[:MAX_ERROR_CHARS] or None, key=f"convert:{doc_id}", **common)
    elif state == "failed":
        await notifications.send(conn, [requested_by], "doc.index_failed", title=f"Couldn't index {name}.",
                                 body=(error or "")[:MAX_ERROR_CHARS] or None, key=f"index:{doc_id}", **common)
    elif state == "indexed" and via == "convert" and conversion_state != "converted":
        # The conversion never ran (the job returned early, or failed and couldn't record that): the document is
        # still indexed from before. Nothing happened, so nothing is said, and no earlier failure is resolved.
        logger.warning("Conversion of %s ended without converting it; no notice sent", doc_id)
    elif state == "indexed" and via == "convert":
        await notifications.resolve(conn, f"convert:{doc_id}")
        await notifications.resolve(conn, f"index:{doc_id}")
        await notifications.send(conn, [requested_by], "doc.converted",
                                 title=f"{name} is converted and ready to chat with.", **common)
    elif state == "indexed":
        await notifications.resolve(conn, f"index:{doc_id}")
        await notifications.send(conn, [requested_by], "doc.indexed", title=f"{name} finished indexing.", **common)
    if shortened:
        try:
            model = (await embedding_state.current(conn)).model
        except Exception:
            model = "The embedding model"
        await notifications.send_admins(
            conn, "embed.shortened",
            title=f"{model} refused {shortened} passages of {canonical}; they were shortened to fit.",
            doc_id=doc_id, target=notifications.ADMIN_MODELS)
