"""/v1/projects/{project_id}/documents — a project's documents and their
versions (V1 spec §5.1). Every route's first check is require_project_role:
a non-member gets 404 before any other id is examined, a member below the
route's role gets 403 insufficient_role, and — as for A0's document routes —
admins act only through a member role. The rules live in
services/doc_versions.py."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps
from ..auth.authz import assert_holds_upload, require_project_role
from ..http_errors import refusal
from ..services import describe_queue, doc_describe, doc_pipeline, doc_storage, doc_versions, helper, index_queue, people
from . import docs as docs_router
from .docs import DOC_ID_PATTERN

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/projects/{project_id}/documents", tags=["projects"])
_reader = deps.require_capability("reader")


class FromLibraryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(pattern=DOC_ID_PATTERN)
    note: str | None = None  # length is clean_note's rule (422 note_too_long), as on the multipart route


class DescribedIn(BaseModel):
    """A project's own tags and description for one of its documents (helper spec §5.8). A field left out stays as
    it is; null goes back to the helper's."""
    model_config = ConfigDict(extra="forbid")
    tags: list[str] | None = None
    description: str | None = None


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: uuid.UUID = Field(alias="from")


def _show_email() -> bool:
    return people.load_directory_config().show_email


async def _state(conn, doc_id: str) -> str:
    cur = await conn.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
    return (await cur.fetchone())[0]


async def _mark_describing(conn, rows: list[dict]) -> list[dict]:
    """Add `describing` (the helper has the row's current version waiting or running) to each row: one query."""
    busy = await describe_queue.waiting_versions(conn, [r["id"] for r in rows])
    for r in rows:
        r["describing"] = r["id"] in busy
    return rows


async def queue_helper(conn, document_id, user_id: str) -> bool:
    """After a filing (helper spec §5.3): queue the helper for the document's new current version, or bring back what
    it wrote for that content in this document before. Never fails the filing: a failure is logged, and the row's
    Generate or "Write tags for N documents" still works. True when a job was queued; the caller wakes the lane."""
    try:
        async with conn.transaction():
            return await doc_describe.queue_version(conn, document_id, requested_by=user_id)
    except Exception:  # noqa: BLE001 — see above
        logger.warning("Could not queue the helper for project document %s", document_id, exc_info=True)
        return False


async def _row(conn, project_id, document_id) -> dict:
    row = await doc_versions.document_row(conn, project_id, document_id, show_email=_show_email())
    if row is None:
        raise refusal(404, "not_found", "Document not found")
    return (await _mark_describing(conn, await index_queue.mark_queued(conn, [row])))[0]


async def _helper_ready(conn, principal: deps.Principal) -> None:
    """A Generate is refused while the helper is off (409) or the person is over today's budget (429): queueing a job
    that would only be dropped tells them nothing."""
    if not (await helper.load_config(conn)).on:
        raise refusal(409, "helper_off", "No helper model is set up. An admin can choose one in Models.")
    if await helper.over_budget(conn, principal.user_id):
        raise refusal(429, "budget_exhausted", "Daily inference budget exhausted.")


@router.get("")
async def list_documents(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn, scope="function")):
    await require_project_role(conn, principal, project_id, None, admin_ok=False)
    rows = await doc_versions.list_project_documents(conn, project_id, show_email=_show_email())
    return await _mark_describing(conn, await index_queue.mark_queued(conn, rows))


@router.post("/describe-missing", status_code=202)
async def describe_missing(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                           conn=Depends(deps.get_conn, scope="function")):
    """Queue the helper for every document here whose current version is indexed and has never been described
    (helper spec §5.6). Contributors and above."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await _helper_ready(conn, principal)
    cur = await conn.execute(
        "SELECT v.doc_id, v.project_doc_id::text FROM project_documents v JOIN documents d ON d.doc_id = v.doc_id "
        "WHERE v.project_id = %s AND v.is_current AND v.verified AND d.state = 'indexed' AND v.gen_at IS NULL "
        "ORDER BY lower(v.file_name), v.project_doc_id", (project_id,))
    queued = 0
    for doc_id, document_id in await cur.fetchall():
        queued += await describe_queue.enqueue(conn, doc_id, project_doc_id=document_id,
                                               requested_by=principal.user_id)
    doc_pipeline.wake()
    return {"queued": queued}


@router.patch("/{document_id}")
async def set_described(project_id: uuid.UUID, document_id: uuid.UUID, body: DescribedIn,
                        principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn, scope="function")):
    """The project's own tags and description for one of its documents (helper spec §5.4). Contributors and above.
    They stay across versions and are seen only in this project; null goes back to the helper's."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    sets, params = [], []
    if "tags" in body.model_fields_set:
        try:
            tags = None if body.tags is None else doc_describe.clean_person_tags(body.tags)
        except ValueError as e:
            raise refusal(422, "too_many_tags", str(e)) from None
        sets.append("tags = %s")
        params.append(tags)
    if "description" in body.model_fields_set:
        try:
            text = None if body.description is None else doc_describe.clean_person_description(body.description)
        except ValueError as e:
            raise refusal(422, "description_too_long", str(e)) from None
        sets.append("description = %s")
        params.append(text)
    if sets:
        cur = await conn.execute(f"UPDATE project_docs SET {', '.join(sets)} WHERE id = %s AND project_id = %s "
                                 "RETURNING id", (*params, document_id, project_id))
        if await cur.fetchone() is None:
            raise refusal(404, "not_found", "Document not found")
    return await _row(conn, project_id, document_id)


@router.post("/{document_id}/describe", status_code=202)
async def describe_document(project_id: uuid.UUID, document_id: uuid.UUID,
                            principal: deps.Principal = Depends(_reader),
                            conn=Depends(deps.get_conn, scope="function")):
    """Generate: ask the helper again for the current version, and clear the project's own words so the new ones
    show (helper spec §5.4; the screen confirms first when there are some). Contributors and above."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    row = await doc_versions.document_row(conn, project_id, document_id, show_email=_show_email())
    if row is None:
        raise refusal(404, "not_found", "Document not found")
    if row["state"] != "indexed":
        raise refusal(409, "not_indexed", "Tags can be written once the document is indexed.")
    await _helper_ready(conn, principal)
    await conn.execute("UPDATE project_docs SET tags = NULL, description = NULL WHERE id = %s", (document_id,))
    await describe_queue.enqueue(conn, row["doc_id"], project_doc_id=str(document_id), requested_by=principal.user_id)
    doc_pipeline.wake()
    return await _row(conn, project_id, document_id)


@router.get("/{document_id}/versions")
async def list_versions(project_id: uuid.UUID, document_id: uuid.UUID,
                        principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn, scope="function")):
    await require_project_role(conn, principal, project_id, None, admin_ok=False)
    return await index_queue.mark_queued(
        conn, await doc_versions.list_versions(conn, project_id, document_id, show_email=_show_email()))


@router.post("/{document_id}/versions", status_code=201)
async def upload_version(project_id: uuid.UUID, document_id: uuid.UUID,
                         file: UploadFile = File(...),
                         file_name: str | None = Form(None), note: str | None = Form(None),
                         principal: deps.Principal = Depends(_reader)):
    """A revised file's bytes become the document's new version (spec §4.4).
    The role and the note are checked before any bytes are stored; A1's
    pipeline stores or dedupes the content and inserts the version as its
    holding, the document locked first; no library entry is added. New
    content is queued for extraction and embedding, as for any upload, and the
    uploader hears how it ended."""
    docs_router._ensure_ready()
    async with docs_router.get_pool().connection() as conn:
        await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    note = doc_versions.clean_note(note)
    name = (file_name or file.filename or "").strip()[:255] or "document"
    staged = await doc_storage.stage_upload(file, name)

    async def prelock(conn):
        await doc_versions.lock_document(conn, project_id, document_id)

    queued = {}

    async def hold(conn, _created):
        out = await doc_versions.add_version(conn, principal.user_id, project_id, document_id,
                                             doc_id=staged.sha256, file_name=name, note=note)
        queued["helper"] = await queue_helper(conn, document_id, principal.user_id)
        return out

    try:
        created, existing, out = await docs_router._store_and_hold(staged, name, hold,
                                                                  prelock=prelock)
        job = await docs_router._after_upload(staged.sha256, staged, created, existing)
    finally:
        doc_storage.discard(staged)  # no-op once placed
    await docs_router._queue_after_upload(staged.sha256, job, principal.user_id)
    if queued.get("helper"):
        doc_pipeline.wake()
    state, index_queued = await docs_router._state_and_queued(staged.sha256)
    return {**out, "state": state, "queued": index_queued}


@router.post("/{document_id}/versions/from-library", status_code=201)
async def version_from_library(project_id: uuid.UUID, document_id: uuid.UUID,
                               body: FromLibraryIn, principal: deps.Principal = Depends(_reader),
                               conn=Depends(deps.get_conn, scope="function")):
    """A file the caller uploaded to their library becomes the new version
    (proof of possession: a verified upload entry, A1 §4)."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await assert_holds_upload(conn, body.doc_id, principal.user_id)
    out = await doc_versions.add_version(
        conn, principal.user_id, project_id, document_id, doc_id=body.doc_id,
        file_name=await doc_versions.filed_name(conn, body.doc_id, principal.user_id),
        note=body.note)
    if await queue_helper(conn, document_id, principal.user_id):
        doc_pipeline.wake()
    return {**out, "state": await _state(conn, out["doc_id"])}


@router.post("/{document_id}/versions/{version}/restore", status_code=201)
async def restore_version(project_id: uuid.UUID, document_id: uuid.UUID, version: int,
                          principal: deps.Principal = Depends(_reader),
                          conn=Depends(deps.get_conn, scope="function")):
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    out = await doc_versions.restore_version(conn, principal.user_id, project_id, document_id,
                                             version)
    if await queue_helper(conn, document_id, principal.user_id):   # usually brings back that version's values
        doc_pipeline.wake()
    return {**out, "state": await _state(conn, out["doc_id"])}


@router.delete("/{document_id}/versions/{version}", status_code=204)
async def delete_version(project_id: uuid.UUID, document_id: uuid.UUID, version: int,
                         principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn, scope="function")):
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.delete_version(conn, principal.user_id, project_id, document_id, version)
    return Response(status_code=204)


@router.post("/{document_id}/merge")
async def merge(project_id: uuid.UUID, document_id: uuid.UUID, body: MergeIn,
                principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn, scope="function")):
    """`document_id` stays; `from` is absorbed into its history (one-way)."""
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.merge_documents(conn, principal.user_id, project_id, document_id,
                                       body.from_)
    return await doc_versions.document_row(conn, project_id, document_id,
                                           show_email=_show_email())


@router.delete("/{document_id}", status_code=204)
async def remove_document(project_id: uuid.UUID, document_id: uuid.UUID,
                          principal: deps.Principal = Depends(_reader),
                          conn=Depends(deps.get_conn, scope="function")):
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.remove_document(conn, principal.user_id, project_id, document_id)
    return Response(status_code=204)
