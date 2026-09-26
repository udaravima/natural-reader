"""/v1/projects — grouping + primary sharing unit. Owner-only project writes; listing
returns owned + member-of. Documents (A1 §5): a holder of an upload entry files
them in; only the project (`can_manage_project_docs`; A1: its owner) removes
them — the uploader has no special power over a placement. Not-permitted is
404 (no existence leak)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field

from ..audit import audit
from ..auth import deps
from ..auth.authz import (
    assert_holds_upload,
    can_manage_project_docs,
    visible_projects_params,
    visible_projects_where,
)
from ..http_errors import refusal
from ..services import doc_content
from .docs import DocId

router = APIRouter(prefix="/v1/projects", tags=["projects"])


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


def _row(r) -> dict:
    return {"id": str(r[0]), "owner_user_id": str(r[1]), "name": r[2],
            "description": r[3]}


async def _assert_owner(conn, project_id: str, user_id: str) -> None:
    cur = await conn.execute(
        "SELECT owner_user_id FROM projects WHERE id = %s", (project_id,))
    row = await cur.fetchone()
    if row is None or str(row[0]) != user_id:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("", status_code=201)
async def create_project(body: ProjectIn,
                         principal: deps.Principal = Depends(deps.get_current_user),
                         conn=Depends(deps.get_conn)):
    cur = await conn.execute(
        "INSERT INTO projects (owner_user_id, name, description) "
        "VALUES (%s,%s,%s) RETURNING id, owner_user_id, name, description",
        (principal.user_id, body.name, body.description))
    out = _row(await cur.fetchone())
    out["is_owner"] = True
    return out


@router.get("")
async def list_projects(principal: deps.Principal = Depends(deps.get_current_user),
                        conn=Depends(deps.get_conn)):
    cur = await conn.execute(
        f"SELECT p.id, p.owner_user_id, p.name, p.description FROM projects p "
        f"WHERE {visible_projects_where('p')} ORDER BY p.created_at DESC",
        visible_projects_params(principal.user_id))
    rows = await cur.fetchall()
    return [{**_row(r), "is_owner": str(r[1]) == principal.user_id} for r in rows]


@router.patch("/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch,
                        principal: deps.Principal = Depends(deps.get_current_user),
                        conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    data = body.model_dump(exclude_unset=True)
    if data:
        sets = ", ".join(f"{k} = %s" for k in data)
        await conn.execute(f"UPDATE projects SET {sets} WHERE id = %s",
                           [*data.values(), project_id])
    cur = await conn.execute(
        "SELECT id, owner_user_id, name, description FROM projects WHERE id = %s",
        (project_id,))
    return {**_row(await cur.fetchone()), "is_owner": True}


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str,
                         principal: deps.Principal = Depends(deps.get_current_user),
                         conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    # Sorted, like admin delete_user's GC: row locks in one order can't deadlock.
    cur = await conn.execute(
        "SELECT doc_id FROM project_documents WHERE project_id = %s ORDER BY doc_id",
        (project_id,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    for doc_id in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="project_deleted")
    return Response(status_code=204)


@router.put("/{project_id}/members/{user_id}", status_code=204)
async def add_member(project_id: str, user_id: str,
                     principal: deps.Principal = Depends(deps.get_current_user),
                     conn=Depends(deps.get_conn)):
    # Owner guard first — a non-owner must 404 before user_id is validated,
    # so an invalid/nonexistent user_id can't be used to probe whether the
    # project itself exists.
    await _assert_owner(conn, project_id, principal.user_id)
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        # Nested transaction (savepoint when already inside one, e.g. the
        # test harness's outer tx) so a caught FK violation rolls back just
        # this INSERT — the connection's transaction stays usable for
        # whatever runs after we return.
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING", (project_id, user_id))
    except pg_errors.ForeignKeyViolation:
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=204)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(project_id: str, user_id: str,
                        principal: deps.Principal = Depends(deps.get_current_user),
                        conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    await conn.execute(
        "DELETE FROM project_members WHERE project_id=%s AND user_id=%s",
        (project_id, user_id))
    return Response(status_code=204)


@router.put("/{project_id}/docs/{doc_id}", status_code=204)
async def link_doc(project_id: uuid.UUID, doc_id: DocId,
                   principal: deps.Principal = Depends(deps.require_capability("reader")),
                   conn=Depends(deps.get_conn)):
    """File a doc into a project. Caller must hold an UPLOAD entry for the doc
    (they proved possession) and be able to SEE the project (owner or member);
    admins skip the visibility check, as the old PATCH project_id path did. The
    project is checked first, so an invisible project 404s before doc ids can
    be used to probe it. A doc someone merely shared with me, or that I only
    read through another project, can't be filed (A1 §5); nor can a pre-A1
    upload entry nobody verified (migration 012). Idempotent: an existing
    placement keeps its original `added_by` — unless it is an UNVERIFIED
    legacy placement, which this verified filing replaces (it would
    otherwise grant the project nothing)."""
    if principal.role == "admin":
        cur = await conn.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
    else:
        cur = await conn.execute(
            f"SELECT 1 FROM projects p WHERE p.id = %s AND {visible_projects_where('p')}",
            [project_id, *visible_projects_params(principal.user_id)])
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await assert_holds_upload(conn, doc_id, principal.user_id)
    cur = await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id, added_by, verified) "
        "VALUES (%s,%s,%s,true) ON CONFLICT (project_id, doc_id) DO UPDATE "
        "SET added_by = EXCLUDED.added_by, verified = true "
        "WHERE NOT project_documents.verified", (project_id, doc_id, principal.user_id))
    if cur.rowcount:
        audit("placement.added", project=project_id, doc=doc_id, by=principal.user_id)
    return Response(status_code=204)


@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def unlink_doc(project_id: uuid.UUID, doc_id: DocId,
                     principal: deps.Principal = Depends(deps.require_capability("reader")),
                     conn=Depends(deps.get_conn)):
    """Remove a doc from a project. Projects govern their documents (A1 §5):
    only `can_manage_project_docs` (A1: the project owner) may — the uploader
    has no special power. Everyone else, and a missing link, gets 404."""
    if not await can_manage_project_docs(conn, principal.user_id, project_id):
        raise refusal(404, "not_found", "Not found")
    cur = await conn.execute(
        "DELETE FROM project_documents WHERE project_id = %s AND doc_id = %s",
        (project_id, doc_id))
    if cur.rowcount == 0:
        raise refusal(404, "not_found", "Not found")
    audit("placement.removed", project=project_id, doc=doc_id, by=principal.user_id)
    await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="placement_removed")
    return Response(status_code=204)
