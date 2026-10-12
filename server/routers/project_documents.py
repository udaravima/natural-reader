"""/v1/projects/{project_id}/documents — a project's documents and their
versions (V1 spec §5.1). Every route's first check is require_project_role:
a non-member gets 404 before any other id is examined, a member below the
route's role gets 403 insufficient_role, and — as for A0's document routes —
admins act only through a member role. The rules live in
services/doc_versions.py."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps
from ..auth.authz import assert_holds_upload, require_project_role
from ..services import doc_storage, doc_versions, people
from . import docs as docs_router
from .docs import DOC_ID_PATTERN

router = APIRouter(prefix="/v1/projects/{project_id}/documents", tags=["projects"])
_reader = deps.require_capability("reader")


class FromLibraryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(pattern=DOC_ID_PATTERN)
    note: str | None = None  # length is clean_note's rule (422 note_too_long), as on the multipart route


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: uuid.UUID = Field(alias="from")


def _show_email() -> bool:
    return people.load_directory_config().show_email


async def _state(conn, doc_id: str) -> str:
    cur = await conn.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
    return (await cur.fetchone())[0]


@router.get("")
async def list_documents(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None, admin_ok=False)
    return await doc_versions.list_project_documents(conn, project_id, show_email=_show_email())


@router.get("/{document_id}/versions")
async def list_versions(project_id: uuid.UUID, document_id: uuid.UUID,
                        principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None, admin_ok=False)
    return await doc_versions.list_versions(conn, project_id, document_id,
                                            show_email=_show_email())


@router.post("/{document_id}/versions", status_code=201)
async def upload_version(project_id: uuid.UUID, document_id: uuid.UUID,
                         background: BackgroundTasks, file: UploadFile = File(...),
                         file_name: str | None = Form(None), note: str | None = Form(None),
                         principal: deps.Principal = Depends(_reader)):
    """A revised file's bytes become the document's new version (spec §4.4).
    The role and the note are checked before any bytes are stored; A1's
    pipeline stores or dedupes the content and inserts the version as its
    holding, the document locked first; no library entry is added. New
    content is extracted and embedded in the background, as for any upload."""
    docs_router._ensure_ready()
    async with docs_router.get_pool().connection() as conn:
        await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    note = doc_versions.clean_note(note)
    name = (file_name or file.filename or "").strip()[:255] or "document"
    staged = await doc_storage.stage_upload(file, name)

    async def prelock(conn):
        await doc_versions.lock_document(conn, project_id, document_id)

    async def hold(conn, _created):
        return await doc_versions.add_version(conn, principal.user_id, project_id, document_id,
                                              doc_id=staged.sha256, file_name=name, note=note)

    try:
        created, existing, out = await docs_router._store_and_hold(staged, name, hold,
                                                                  prelock=prelock)
        job = await docs_router._after_upload(staged.sha256, staged, created, existing)
    finally:
        doc_storage.discard(staged)  # no-op once placed
    if job is not None:
        background.add_task(job, staged.sha256)
    async with docs_router.get_pool().connection() as conn:
        return {**out, "state": await _state(conn, staged.sha256)}


@router.post("/{document_id}/versions/from-library", status_code=201)
async def version_from_library(project_id: uuid.UUID, document_id: uuid.UUID,
                               body: FromLibraryIn, principal: deps.Principal = Depends(_reader),
                               conn=Depends(deps.get_conn)):
    """A file the caller uploaded to their library becomes the new version
    (proof of possession: a verified upload entry, A1 §4)."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await assert_holds_upload(conn, body.doc_id, principal.user_id)
    out = await doc_versions.add_version(
        conn, principal.user_id, project_id, document_id, doc_id=body.doc_id,
        file_name=await doc_versions.filed_name(conn, body.doc_id, principal.user_id),
        note=body.note)
    return {**out, "state": await _state(conn, out["doc_id"])}


@router.post("/{document_id}/versions/{version}/restore", status_code=201)
async def restore_version(project_id: uuid.UUID, document_id: uuid.UUID, version: int,
                          principal: deps.Principal = Depends(_reader),
                          conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    out = await doc_versions.restore_version(conn, principal.user_id, project_id, document_id,
                                             version)
    return {**out, "state": await _state(conn, out["doc_id"])}


@router.delete("/{document_id}/versions/{version}", status_code=204)
async def delete_version(project_id: uuid.UUID, document_id: uuid.UUID, version: int,
                         principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.delete_version(conn, principal.user_id, project_id, document_id, version)
    return Response(status_code=204)


@router.post("/{document_id}/merge")
async def merge(project_id: uuid.UUID, document_id: uuid.UUID, body: MergeIn,
                principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """`document_id` stays; `from` is absorbed into its history (one-way)."""
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.merge_documents(conn, principal.user_id, project_id, document_id,
                                       body.from_)
    return await doc_versions.document_row(conn, project_id, document_id,
                                           show_email=_show_email())


@router.delete("/{document_id}", status_code=204)
async def remove_document(project_id: uuid.UUID, document_id: uuid.UUID,
                          principal: deps.Principal = Depends(_reader),
                          conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.remove_document(conn, principal.user_id, project_id, document_id)
    return Response(status_code=204)
