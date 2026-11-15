"""/v1/projects/{project_id}/uploads — many files into a project (bulk upload spec, phase 2).

`plan` says what each file would become and stores nothing; the upload route stores and files one file. Both decide
through services/bulk_upload.decide, so the screen and a script follow the same rules. Both need a Contributor role
(admins only through a member role, as for filing), and accept a session or a token with the `documents` scope."""
from __future__ import annotations

import base64
import binascii
import math
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import deps
from ..auth.authz import require_project_role
from ..http_errors import refusal
from ..services import bulk_upload, doc_storage, doc_versions, people
from . import docs as docs_router

router = APIRouter(prefix="/v1/projects/{project_id}/uploads", tags=["projects"])
_reader = deps.require_capability("reader")


class PlanFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=4096)
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern="^[0-9a-fA-F]{64}$")
    head: str | None = Field(default=None, max_length=64)  # the first 5 bytes, base64

    @field_validator("head")
    @classmethod
    def _head_is_base64(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("head must be base64") from None
        return value


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[PlanFileIn] = Field(max_length=10_000)  # a body this big is a 422 before the limit's 413


def _show_email() -> bool:
    return people.load_directory_config().show_email


def _not_enough_space(needed_mb: int, free: int, reserve: int):
    return refusal(507, "not_enough_space",
                   f"This upload needs {needed_mb} MB, and the server has {free} MB free, "
                   f"of which it keeps {reserve} MB in reserve.",
                   needed_mb=needed_mb, free_mb=free, reserve_mb=reserve)


@router.post("/plan")
async def plan(project_id: uuid.UUID, body: PlanIn, principal: deps.Principal = Depends(_reader),
               conn=Depends(deps.get_conn, scope="function")):
    """What each file would become here, in the order given. The hash is a claim used only to plan: the upload
    hashes the bytes itself. A repeated name inside the list is the caller's to handle; each file is judged
    against the project only."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    limit = bulk_upload.max_files()
    if len(body.files) > limit:
        raise refusal(413, "too_many_files",
                      f"At most {limit} files can be uploaded at once. Upload them in smaller groups.", limit=limit)
    show_email = _show_email()
    answers, needed = [], 0
    for f in body.files:
        name = bulk_upload.clean_name(f.name)
        head = base64.b64decode(f.head) if f.head is not None else None
        refused = bulk_upload.refusal_for(name, f.size, head)
        out: dict = {"name": name, "action": "refused", "document": None, "version": None, "candidates": [],
                     "code": None, "message": None}
        if refused:
            out.update(refused)
        else:
            d = await bulk_upload.decide(conn, project_id, name=name,
                                         sha256=f.sha256.lower() if f.sha256 else None, show_email=show_email)
            out.update(action=d.action, document=d.document, version=d.version, candidates=list(d.candidates))
            if d.action != "unchanged":
                needed += f.size
        answers.append(out)
    if needed:
        needed_mb, reserve = math.ceil(needed / bulk_upload.MIB), bulk_upload.reserve_mb()
        free = bulk_upload.free_mb()
        if free < needed_mb + reserve:
            raise _not_enough_space(needed_mb, free, reserve)
    return {"files": answers}


@router.post("", status_code=201)
async def upload(project_id: uuid.UUID, response: Response, file: UploadFile = File(...),
                 on_match: Literal["version", "separate", "skip"] = Form("version"),
                 on_older: Literal["leave", "restore"] = Form("leave"),
                 document_id: uuid.UUID | None = Form(None), note: str | None = Form(None),
                 describe: bool = Form(True),
                 principal: deps.Principal = Depends(_reader)):
    """Store one file and file it here, in one step: the steps of Upload new version, deciding after the bytes are
    hashed and before content is written. `describe=false` indexes it without asking the helper for tags. 201 when something was filed (created, version, restored); 200 when
    nothing was (unchanged, skipped, left_older, ambiguous), and then nothing is stored. No library entry is added.
    New content is queued for indexing, and the uploader hears how it ended."""
    docs_router._ensure_ready()
    async with docs_router.get_pool().connection() as conn:
        await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    note = doc_versions.clean_note(note)
    name = bulk_upload.clean_name(file.filename)
    staged = await doc_storage.stage_upload(file, name)
    decided: dict = {}

    async def decide_and_lock(conn):
        decided["outcome"] = o = await bulk_upload.decide_and_lock(
            conn, principal.user_id, project_id, name=name, sha256=staged.sha256, document_id=document_id,
            on_match=on_match, on_older=on_older, show_email=_show_email())
        return None if o.stores else docs_router.STORE_NOTHING

    async def hold(conn, _created):
        return await bulk_upload.file_it(conn, principal.user_id, project_id, decided["outcome"],
                                         doc_id=staged.sha256, name=name, note=note)

    try:
        free, reserve = bulk_upload.free_mb(), bulk_upload.reserve_mb()
        if free < reserve:  # the staged file already holds its space; a script that skips the plan lands here
            raise _not_enough_space(math.ceil(staged.size / bulk_upload.MIB), free, reserve)
        created, existing, document = await docs_router._store_and_hold(staged, name, hold,
                                                                       prelock=decide_and_lock)
        job = None
        if document is not docs_router.STORE_NOTHING:
            job = await docs_router._after_upload(staged.sha256, staged, created, existing)
    finally:
        doc_storage.discard(staged)  # no-op once placed
    outcome: bulk_upload.Outcome = decided["outcome"]
    d = outcome.decision
    answer = {"result": outcome.result, "document": d.document, "doc_id": staged.sha256, "state": None,
              "queued": False, "candidates": list(d.candidates)}
    if outcome.stores:
        # `describe`: the panel's "Write tags and descriptions" tick (helper spec §5.3); the project gives the tags
        # the helper is shown to reuse.
        await docs_router._queue_after_upload(staged.sha256, job, principal.user_id,
                                              options={"describe": describe, "project_id": str(project_id)})
        answer["document"] = document
    else:
        response.status_code = 200
        if outcome.result == "left_older":
            answer["older_version"] = d.version
    if outcome.result in ("created", "version", "restored", "unchanged", "left_older"):
        answer["state"], answer["queued"] = await docs_router._state_and_queued(staged.sha256)
    return answer
