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

import logging
import re
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Path as PathParam, Response,
    UploadFile,
)
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
from ..services import doc_content, doc_pipeline, doc_storage, docling_convert
from ..services.embeddings import embed_one


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/docs", tags=["docs"])

# doc_id is the server-computed sha256 hex digest of the content's bytes.
# Path parameters are constrained to exactly that shape (SEC-1): FastAPI
# otherwise accepts arbitrary strings, and a doc id ends up in SQL, logs and
# (via doc_storage.place) a filesystem path.
DOC_ID_PATTERN = r"^[0-9a-f]{64}$"
DocId = Annotated[str, PathParam(pattern=DOC_ID_PATTERN)]


# ---------- request / response models ----------

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


# Background jobs live in services/doc_pipeline.py. The old private names are
# kept as aliases so the convert job below and existing call sites resolve.
_doc_job_locks = doc_pipeline._doc_job_locks
_get_doc_lock = doc_pipeline.doc_lock
_run_index_job = doc_pipeline.run_embed
_chunks_from_pages = doc_pipeline.chunks_from_pages
# Back-compat aliases for the original PR 4 names.
_index_locks = _doc_job_locks
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


async def _lock_existing(conn, doc_id: str):
    """Lock existing content FOR SHARE before adding an entry, so a concurrent
    GC (FOR UPDATE) either finishes first — we then see no row — or waits for
    us. Returns (state, extracted_by, bytes_path, conversion_state) or None."""
    cur = await conn.execute(
        "SELECT state, extracted_by, bytes_path, conversion_state FROM documents "
        "WHERE doc_id = %s FOR SHARE", (doc_id,))
    return await cur.fetchone()


@router.post("")
async def register_document(
    background: BackgroundTasks,
    response: Response,
    file: UploadFile = File(...),
    file_name: str | None = Form(None),
    tags: list[str] = Form(default=[]),
    client_doc_id: str | None = Form(None),
    principal: Principal = Depends(require_capability("reader")),
) -> dict[str, Any]:
    """Add a file to my library (A1 spec §4). The server hashes the uploaded
    bytes — that hash IS the doc id; `client_doc_id` is only a hint. Existing
    content → 200 dedupe, no rework. New content → 202 and a background
    extract + embed job. Proof of possession: nobody gets an entry without
    sending the bytes."""
    _ensure_ready()
    name = (file_name or file.filename or "").strip()[:255] or "document"
    staged = await doc_storage.stage_upload(file, name)
    try:
        doc_id = staged.sha256
        if client_doc_id and client_doc_id != doc_id:
            # The hint is free text from the client: log it only when it has
            # the shape of an id, so a WARNING line never carries anything else.
            hint = client_doc_id if re.fullmatch(DOC_ID_PATTERN, client_doc_id) else "(malformed)"
            logger.warning("Client hash hint mismatch for user %s: hint %s, server %s",
                           principal.user_id, hint, doc_id)
        created, existing = await _add_upload_entry(principal.user_id, staged, name, tags)
        job = await _after_upload(doc_id, staged, created, existing)
    finally:
        doc_storage.discard(staged)  # no-op once placed
    if job is not None:
        background.add_task(job, doc_id)
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
        state = (await cur.fetchone())[0]
    scheduled_new_work = job is doc_pipeline.run_pipeline
    response.status_code = 202 if scheduled_new_work else 200
    return {"doc_id": doc_id, "dedup": not created, "state": state}


async def _add_upload_entry(user_id: str, staged, name: str, tags: list[str]):
    """Create-or-find the content and give the user an upload entry, in one
    transaction. Retries ONCE if a GC removed the row between our conflicting
    INSERT and our lock (spec §3, races) — the caller never sees a 5xx."""
    for attempt in (1, 2):
        async with get_pool().connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                    "extracted_by) VALUES (%s,%s,%s,%s,'extracting','server') "
                    "ON CONFLICT (doc_id) DO NOTHING RETURNING doc_id",
                    (staged.sha256, name, staged.file_type, staged.size))
                created = await cur.fetchone() is not None
                existing = None
                if not created:
                    existing = await _lock_existing(conn, staged.sha256)
                    if existing is None:
                        if attempt == 1:
                            logger.warning("Upload of %s lost a race with GC; retrying once",
                                           staged.sha256)
                            continue
                        raise refusal(503, "busy", "Please try again.")
                outcome = await doc_content.add_entry(
                    conn, user_id, staged.sha256, via="upload", tags=tags,
                    file_name=None if created else name)
        if outcome != "exists":
            audit("entry.added", user=user_id, doc=staged.sha256, via="upload",
                  dedup=str(not created).lower())
        return created, existing
    raise AssertionError("unreachable")


async def _after_upload(doc_id: str, staged, created: bool, existing):
    """Put verified bytes in place (after the INSERT committed) and decide
    what, if anything, runs next. Returns the job to schedule, or None."""
    if created:
        await _set_bytes_path(doc_id, doc_storage.place(staged, doc_id))
        return doc_pipeline.run_pipeline
    state, extracted_by, bytes_path, conversion_state = existing
    old_file_ok = bool(bytes_path) and doc_storage.sha256_file(Path(bytes_path)) == doc_id
    if not old_file_ok or extracted_by == "client":
        if bytes_path and not Path(bytes_path).exists():
            logger.warning("Content %s had missing bytes; restored from this upload", doc_id)
        await _set_bytes_path(doc_id, doc_storage.place(staged, doc_id))
    if extracted_by == "client":
        if state == "indexed":
            if conversion_state == "converted" and old_file_ok:
                # Its converted pages came from bytes that hash to the id: already
                # server-derived. Trust it without rework.
                async with get_pool().connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET extracted_by = 'server', updated_at = now() "
                        "WHERE doc_id = %s", (doc_id,))
                return None
            return doc_pipeline.run_legacy_swap
        await _set_state(doc_id, "stored")
        return doc_pipeline.run_pipeline
    if state in ("stored", "failed", "registered"):
        await _set_state(doc_id, "stored")
        return doc_pipeline.run_pipeline
    if state == "extracted":
        return doc_pipeline.run_pipeline
    return None  # extracting / indexing / indexed: dedupe, nothing to redo


async def _set_bytes_path(doc_id: str, path: Path) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE documents SET bytes_path = %s, updated_at = now() WHERE doc_id = %s",
            (str(path), doc_id))


async def _set_state(doc_id: str, state: str) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE documents SET state = %s, error_message = NULL, updated_at = now() "
            "WHERE doc_id = %s", (state, doc_id))


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

_CONTENT_SHARED_MESSAGES = {
    "other_holders": "Other people also use this document, so it can't be changed here. Ask an admin.",
    "in_project": ("This document is in a project, so changing it would change it for the project "
                   "too. Remove it from the project first, or ask an admin."),
}


def _content_shared(reason: str, doc_id: str, user_id: str) -> HTTPException:
    logger.warning("content_shared refusal: user %s doc %s reason %s", user_id, doc_id, reason)
    return refusal(409, "content_shared", _CONTENT_SHARED_MESSAGES[reason], reason=reason)


@router.post("/{doc_id}/index", status_code=202)
async def start_index_job(doc_id: DocId, background: BackgroundTasks,
                          principal: Principal = Depends(_require_doc_reader)) -> dict[str, Any]:
    """Resume or re-index (spec §4). On content that isn't `indexed`, any entry
    holder — or an admin, even without one — may RESUME it (a crash, a
    failure). On `indexed` content this is a RE-INDEX from the stored bytes —
    a content-changing op, so only the sole holder or an admin (else 409
    content_shared), and only when there's something to rebuild from: no
    stored bytes and no conversion means re-indexing would destroy a working
    index, so that's refused too (409 bytes_missing), state untouched."""
    _ensure_ready()
    is_admin = principal.role == "admin"
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT state, bytes_path, conversion_state FROM documents WHERE doc_id = %s",
            (doc_id,))
        row = await cur.fetchone()
        if row is None:
            raise refusal(404, "not_found", "Document not found")
        state, bytes_path, conversion_state = row
        if state == "indexed":
            reason = await doc_content.content_ops_refusal(
                conn, doc_id, principal.user_id, is_admin=is_admin)
            if reason:
                raise _content_shared(reason, doc_id, principal.user_id)
            has_bytes = bool(bytes_path) and Path(bytes_path).is_file()
            if not has_bytes and conversion_state != "converted":
                raise refusal(409, "bytes_missing", "Upload the file again first.")
        elif not is_admin and not await doc_content.holds_entry(conn, principal.user_id, doc_id):
            raise refusal(404, "not_found", "Document not found")
        if state in ("extracting", "indexing"):
            return {"ok": True, "doc_id": doc_id, "state": state}  # already running
        if state in ("indexed", "failed", "registered"):
            await conn.execute(
                "UPDATE documents SET state = 'stored', error_message = NULL, updated_at = now() "
                "WHERE doc_id = %s", (doc_id,))
    background.add_task(doc_pipeline.run_pipeline, doc_id)
    return {"ok": True, "doc_id": doc_id, "state": "extracting"}


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
