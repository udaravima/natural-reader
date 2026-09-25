"""
Document content, library entries and the jobs that derive text/embeddings.

`documents` is content owned by nobody; a user holds it through a library
entry (`library_entries`), a project through a placement (`project_documents`).
Who-holds-what writes and garbage collection live in `services/doc_content.py`
(A1 spec §3, §5). The content state machine (spec §4):

    stored → extracting → extracted → indexing → indexed | failed

`registered` is legacy only: rows from the client-chunk era with neither
bytes nor chunks, until their first verified upload.

Chunk inserts are idempotent on (doc_id, text_hash) so re-running the Index
button is safe — the existing rows are upserted in place.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Path as PathParam, Response, UploadFile
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from ..audit import audit
from ..auth.authz import (
    assert_can_read_doc,
    assert_holds_upload,
    readable_docs_params,
    readable_docs_where,
    visible_projects_params,
    visible_projects_where,
)
from ..auth.deps import Principal, require_capability
from ..db import get_pool, is_ready
from ..http_errors import refusal
from ..services import doc_content, docling_convert, model_router
from ..services.embeddings import EMBEDDING_DIM, embed_batch, embed_one


# Filesystem location for retained PDF bytes. Overridable via env so the
# docker-compose mount can park them on a named volume in production.
PDF_STORAGE_DIR = Path(os.environ.get("PDF_STORAGE_DIR", "./data/pdfs")).resolve()
PDF_UPLOAD_MAX_MB = int(os.environ.get("PDF_UPLOAD_MAX_MB", "50"))

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/docs", tags=["docs"])

# doc_id is a client-supplied sha256 hex digest. Constrain it to exactly that
# shape everywhere it appears (SEC-1): the value is interpolated into a
# filesystem path (`_pdf_storage_path`), so an unconstrained value like
# "../../etc/x" would escape PDF_STORAGE_DIR. `DocId` applies the same rule to
# path parameters, which FastAPI otherwise accepts as arbitrary strings.
DOC_ID_PATTERN = r"^[0-9a-f]{64}$"
DocId = Annotated[str, PathParam(pattern=DOC_ID_PATTERN)]


# ---------- request / response models ----------

class DocRegisterIn(BaseModel):
    doc_id: str = Field(pattern=DOC_ID_PATTERN)
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


class DocPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_name: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None


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


def _doc_projects_sql(alias: str) -> str:
    """JSON array `[{id, name}]` of the projects `<alias>` is placed in that the
    caller can see. Everyone — including whoever uploaded it — sees only
    projects they own or belong to: listing others would leak their names, and
    under A1 an uploader can't unlink from a project anyway. Sorted by name,
    then id. Bind with `_doc_projects_params(user_id)`; SELECT-list params come
    BEFORE join and WHERE params."""
    return (
        "COALESCE((SELECT json_agg(json_build_object('id', _vp.id, 'name', _vp.name) "
        "ORDER BY _vp.name, _vp.id) "
        "FROM project_documents _vpd JOIN projects _vp ON _vp.id = _vpd.project_id "
        f"WHERE _vpd.doc_id = {alias}.doc_id AND {visible_projects_where('_vp')}), '[]'::json)"
    )


def _doc_projects_params(user_id: str) -> list[str]:
    return visible_projects_params(user_id)


# The caller's own entry (if any) and who shared it. Bind `_ENTRY_JOIN` with
# [user_id], after SELECT-list params and before WHERE params.
_ENTRY_JOIN = (
    "LEFT JOIN library_entries _me ON _me.doc_id = d.doc_id AND _me.user_id = %s "
    "LEFT JOIN users _sb ON _sb.id = _me.shared_by"
)
_ENTRY_COLS = (
    "COALESCE(_me.file_name, d.file_name) AS file_name, "
    "COALESCE(_me.tags, '{}') AS tags, _me.added_via AS added_via, "
    "_me.shared_by AS shared_by_id, COALESCE(_sb.display_name, _sb.email) AS shared_by_name"
)


def _entry_fields(rec: dict[str, Any]) -> dict[str, Any]:
    sid, sname = rec.pop("shared_by_id", None), rec.pop("shared_by_name", None)
    rec["shared_by"] = {"id": str(sid), "name": sname} if sid else None
    rec["in_library"] = rec.get("added_via") is not None
    return rec


async def _fetch_doc_status(conn, doc_id: str, user_id: str) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT d.doc_id, d.file_type, d.size_bytes, d.page_count,
                   d.state, d.embedding_model, d.embedding_dim, d.error_message,
                   d.created_at, d.updated_at,
                   d.conversion_state, d.conversion_options, d.conversion_error,
                   d.converted_at, d.bytes_path, {_ENTRY_COLS},
                   {_doc_projects_sql('d')} AS projects,
                   COALESCE(c.cnt, 0) AS chunk_count,
                   COALESCE(c.embedded, 0) AS embedded_count,
                   COALESCE(p.page_cnt, 0) AS converted_page_count
            FROM documents d
            {_ENTRY_JOIN}
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
            [*_doc_projects_params(user_id), user_id, doc_id],
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
    rec["has_pdf"] = bool(rec.pop("bytes_path", None)) and rec["file_type"] == "pdf"
    return _entry_fields(rec)


# ---------- authz ----------

async def _require_upload_holder(
    doc_id: DocId,
    principal: Principal = Depends(require_capability("reader")),
) -> Principal:
    """Route dependency: 401 if unauthenticated, 403 if the caller lacks the
    `reader` capability, 404 unless the caller holds an UPLOAD entry for the
    doc (missing and not-held are indistinguishable to the caller)."""
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await assert_holds_upload(conn, doc_id, principal.user_id)
    return principal


async def _require_doc_reader(
    doc_id: DocId,
    principal: Principal = Depends(require_capability("reader")),
) -> Principal:
    """Read gate: 403 if the caller lacks the `reader` capability, then a
    library entry for the doc, or owner/member of any project it is placed in
    (404 otherwise — missing and not-readable are indistinguishable to the
    caller). See `server/auth/authz.py`'s `readable_docs_where`."""
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await assert_can_read_doc(conn, doc_id, principal.user_id)
    return principal


# ---------- routes ----------

@router.get("")
async def list_documents(
    q: str | None = None,
    project_id: uuid.UUID | None = None,
    tag: str | None = None,
    principal: Principal = Depends(require_capability("reader")),
) -> list[dict[str, Any]]:
    """
    List docs in my library (uploaded or shared with me) plus docs placed in
    projects I own or belong to. Access is resolved in SQL via
    `readable_docs_where` — never post-filtered in Python — so a doc nobody
    gave me can never appear, even if it happens to match `q`/`tag`. Name,
    tags, `added_via` and `shared_by` come from MY entry; a project-only row
    has `in_library: false` and the content's canonical name.
    """
    _ensure_ready()
    uid = principal.user_id
    where = [readable_docs_where("d")]
    where_params: list[Any] = readable_docs_params(uid)
    if q:
        where.append("(COALESCE(_me.file_name, d.file_name) ILIKE %s OR %s = ANY(_me.tags))")
        where_params += [f"%{q}%", q]
    if project_id:
        # Only a project the caller can see: filtering a shared doc by a
        # guessed project id must not reveal whether someone placed it there.
        where.append(
            "EXISTS (SELECT 1 FROM project_documents _fpd "
            "JOIN projects _fp ON _fp.id = _fpd.project_id "
            f"WHERE _fpd.doc_id = d.doc_id AND _fpd.project_id = %s "
            f"AND {visible_projects_where('_fp')})"
        )
        where_params += [project_id, *visible_projects_params(uid)]
    if tag:
        where.append("%s = ANY(_me.tags)")
        where_params.append(tag)
    sql = (
        f"SELECT d.doc_id, d.state, {_ENTRY_COLS}, {_doc_projects_sql('d')} AS projects "
        f"FROM documents d {_ENTRY_JOIN} "
        f"WHERE {' AND '.join(where)} ORDER BY d.updated_at DESC"
    )
    # psycopg binds by position: SELECT-list params, then the join, then WHERE.
    params = [*_doc_projects_params(uid), uid, *where_params]
    pool = get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in await cur.fetchall()]
    return [_entry_fields(r) for r in rows]


@router.post("")
async def register_document(
    payload: DocRegisterIn,
    principal: Principal = Depends(require_capability("reader")),
) -> dict[str, Any]:
    """
    Interim JSON registration (Task 9 replaces it with a verified multipart
    upload): creates the content row if new — an existing row's metadata is
    left alone — and gives the caller an upload entry for it.
    """
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, page_count) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (doc_id) DO NOTHING",
            (payload.doc_id, payload.file_name, payload.file_type, payload.size_bytes,
             payload.page_count))
        if await doc_content.add_entry(conn, principal.user_id, payload.doc_id, via="upload") != "exists":
            audit("entry.added", user=principal.user_id, doc=payload.doc_id, via="upload")
        status = await _fetch_doc_status(conn, payload.doc_id, principal.user_id)
    return status


@router.get("/{doc_id}")
async def get_document(
    doc_id: DocId, reader: Principal = Depends(_require_doc_reader)
) -> dict[str, Any]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        status = await _fetch_doc_status(conn, doc_id, reader.user_id)
    if not status:
        raise HTTPException(status_code=404, detail="Document not found")
    return status


@router.patch("/{doc_id}")
async def patch_document(
    doc_id: DocId, body: DocPatchIn,
    principal: Principal = Depends(require_capability("reader")),
) -> dict[str, Any]:
    """
    Rename or retag MY library entry. Other holders' names and tags, and the
    content itself, are untouched. `file_name` "" or null resets to the
    content's canonical name; `tags` null clears them. 404 unless I hold an
    entry (seeing a doc only through a project doesn't count).

    Content has no owner, so the old owner-reassignment field (and the admin
    path behind it) is gone — sending it is a 422 (extra="forbid"), as is
    `project_id`: placements are managed by PUT/DELETE
    /v1/projects/{id}/docs/{doc_id}.
    """
    _ensure_ready()
    data = body.model_dump(exclude_unset=True)
    pool = get_pool()
    async with pool.connection() as conn:
        if not await doc_content.holds_entry(conn, principal.user_id, doc_id):
            raise refusal(404, "not_found", "Document not found")
        sets, params = [], []
        if "tags" in data:
            # `{"tags": null}` is schema-valid and means "clear all tags".
            sets.append("tags = %s"); params.append(sorted(set(data["tags"] or [])))
        if "file_name" in data:
            # "" or null resets to the content's canonical name
            sets.append("file_name = %s"); params.append((data["file_name"] or "").strip() or None)
        if sets:
            await conn.execute(
                f"UPDATE library_entries SET {', '.join(sets)} WHERE user_id = %s AND doc_id = %s",
                [*params, principal.user_id, doc_id])
        status = await _fetch_doc_status(conn, doc_id, principal.user_id)
    if not status:
        raise HTTPException(status_code=404, detail="Document not found")
    return status


@router.post("/{doc_id}/chunks")
async def upload_chunks(
    doc_id: DocId,
    payload: ChunksUploadIn,
    holder: Principal = Depends(_require_upload_holder),
) -> dict[str, Any]:
    """
    Bulk insert/upsert chunks. The frontend should call this in batches (~50)
    rather than one giant payload — keeps individual requests bounded and lets
    the UI show incremental progress.

    Chunks are upserted on (doc_id, text_hash): re-indexing the same doc reuses
    existing rows (preserving any embeddings from PR 4) rather than duplicating.
    The doc's state advances to `extracted` on the first successful batch
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
            # rewind a doc that's already `indexed` back to `extracted`.
            if current_state in (None, "registered"):
                await conn.execute(
                    "UPDATE documents SET state = 'extracted', updated_at = now() WHERE doc_id = %s",
                    (doc_id,),
                )

        status = await _fetch_doc_status(conn, doc_id, holder.user_id)

    return {"ok": True, "inserted": len(payload.chunks), "doc_id": doc_id, "status": status}


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: DocId, principal: Principal = Depends(require_capability("reader"))
):
    """Remove the document from MY library. Other people's entries and project
    placements are untouched; the content itself goes only when nobody holds
    it any more (spec §3 GC)."""
    _ensure_ready()
    async with get_pool().connection() as conn:
        if not await doc_content.remove_entry(conn, principal.user_id, doc_id):
            raise refusal(404, "not_found", "Document not found")
        audit("entry.removed", user=principal.user_id, doc=doc_id)
        if await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="entry_removed"):
            _doc_job_locks.pop(doc_id, None)
    return Response(status_code=204)


@router.put("/{doc_id}/shares/{user_id}", status_code=204)
async def add_share(doc_id: DocId, user_id: str,
                    principal: Principal = Depends(_require_upload_holder)):
    """Share with one user: creates their `shared` entry. Only an upload-entry
    holder may share (they proved possession); recipients can't re-share. A
    recipient who already holds the content keeps their entry unchanged."""
    _ensure_ready()
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise refusal(404, "not_found", "User not found")
    try:
        async with get_pool().connection() as conn:
            async with conn.transaction():  # savepoint: a caught FK error leaves conn usable
                outcome = await doc_content.add_entry(
                    conn, user_id, doc_id, via="shared", shared_by=principal.user_id)
    except pg_errors.ForeignKeyViolation:
        raise refusal(404, "not_found", "User not found")
    if outcome == "created":
        audit("share.created", by=principal.user_id, to=user_id, doc=doc_id)
    return Response(status_code=204)


@router.delete("/{doc_id}/shares/{user_id}", status_code=204)
async def remove_share(doc_id: DocId, user_id: str,
                       principal: Principal = Depends(require_capability("reader"))):
    """Revoke a share I created. Never removes someone's own upload or another
    sharer's share (404). Recipients remove their own copy via DELETE /{id}."""
    _ensure_ready()
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise refusal(404, "not_found", "User not found")
    async with get_pool().connection() as conn:
        if not await doc_content.revoke_share(conn, principal.user_id, user_id, doc_id):
            raise refusal(404, "not_found", "Share not found")
        audit("share.revoked", by=principal.user_id, to=user_id, doc=doc_id)
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="share_revoked")
    return Response(status_code=204)


# ---------- embeddings + retrieval ----------

async def _run_index_job(doc_id: str) -> None:
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


@router.post("/{doc_id}/index", status_code=202)
async def start_index_job(
    doc_id: DocId,
    background: BackgroundTasks,
    _holder: Principal = Depends(_require_upload_holder),
) -> dict[str, Any]:
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
async def search_document(
    doc_id: DocId,
    req: SearchIn,
    _reader: Principal = Depends(_require_doc_reader),
) -> dict[str, Any]:
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
    # Defense in depth behind DocId validation: resolve and confirm the path
    # stays inside PDF_STORAGE_DIR, so a doc_id that ever slips past the hex
    # pattern still can't escape via `../` (SEC-1).
    path = (PDF_STORAGE_DIR / f"{doc_id}.pdf").resolve()
    if not path.is_relative_to(PDF_STORAGE_DIR):
        raise HTTPException(status_code=400, detail="Invalid doc_id")
    return path


@router.post("/{doc_id}/pdf")
async def upload_pdf_bytes(
    doc_id: DocId,
    file: UploadFile = File(...),
    _holder: Principal = Depends(_require_upload_holder),
) -> dict[str, Any]:
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
            "UPDATE documents SET bytes_path = %s, updated_at = now() WHERE doc_id = %s",
            (str(path), doc_id),
        )
    return {"ok": True, "doc_id": doc_id, "size_bytes": written}


@router.delete("/{doc_id}/pdf")
async def delete_pdf_bytes(
    doc_id: DocId, _holder: Principal = Depends(_require_upload_holder)
) -> dict[str, Any]:
    """Remove retained PDF bytes for `doc_id`. Keeps converted markdown / chunks."""
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT bytes_path FROM documents WHERE doc_id = %s", (doc_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        bytes_path = row[0]
        await cur.execute(
            "UPDATE documents SET bytes_path = NULL, updated_at = now() WHERE doc_id = %s",
            (doc_id,),
        )
    if bytes_path:
        try:
            Path(bytes_path).unlink(missing_ok=True)
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
                    "SELECT bytes_path FROM documents WHERE doc_id = %s",
                    (doc_id,),
                )
                row = await cur.fetchone()
            if not row or not row[0]:
                raise RuntimeError("No retained PDF for this document")

            pages = await docling_convert.convert_pdf_to_markdown_pages(Path(row[0]), options)
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
    doc_id: DocId,
    options: ConvertOptionsIn,
    background: BackgroundTasks,
    _holder: Principal = Depends(_require_upload_holder),
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
            "SELECT bytes_path FROM documents WHERE doc_id = %s",
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
async def get_document_markdown(
    doc_id: DocId,
    page: int | None = None,
    _reader: Principal = Depends(_require_doc_reader),
) -> Response:
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
async def delete_document_markdown(
    doc_id: DocId, _holder: Principal = Depends(_require_upload_holder)
) -> dict[str, Any]:
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
