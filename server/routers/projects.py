"""/v1/projects — grouping + primary sharing unit. Owner-only project writes; listing
returns owned + member-of. Doc links: doc owners add, doc owners or project owners remove.
Not-permitted is 404 (no existence leak)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps
from ..auth.authz import assert_owns_doc, visible_projects_params, visible_projects_where
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
        "SELECT id, owner_user_id, name, description FROM projects "
        "WHERE owner_user_id = %s OR id IN "
        "(SELECT project_id FROM project_members WHERE user_id = %s) "
        "ORDER BY created_at DESC",
        (principal.user_id, principal.user_id))
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
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
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
    """File a doc into a project. Caller must OWN the doc and be able to SEE
    the project (owner or member); admins skip the visibility check, as the
    old PATCH project_id path did. The project is checked first, so an
    invisible project 404s before doc ids can be used to probe it. Only doc
    owners add — a project owner can't pull in a doc they merely read (spec §6)."""
    if principal.role == "admin":
        cur = await conn.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
    else:
        cur = await conn.execute(
            f"SELECT 1 FROM projects p WHERE p.id = %s AND {visible_projects_where('p')}",
            [project_id, *visible_projects_params(principal.user_id)])
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await assert_owns_doc(conn, doc_id, principal.user_id)
    await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING", (project_id, doc_id))
    return Response(status_code=204)


@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def unlink_doc(project_id: uuid.UUID, doc_id: DocId,
                     principal: deps.Principal = Depends(deps.require_capability("reader")),
                     conn=Depends(deps.get_conn)):
    """Remove a doc from a project. The doc owner always gets 204 (idempotent,
    and it reveals nothing about the project). The project owner gets 204 only
    when the link exists — they can already see their own project's docs.
    Everyone else, including plain members, gets 404."""
    cur = await conn.execute("SELECT user_id FROM documents WHERE doc_id = %s", (doc_id,))
    row = await cur.fetchone()
    if row is not None and str(row[0]) == principal.user_id:
        await conn.execute(
            "DELETE FROM project_documents WHERE project_id = %s AND doc_id = %s",
            (project_id, doc_id))
        return Response(status_code=204)
    cur = await conn.execute(
        "DELETE FROM project_documents pd USING projects p "
        "WHERE pd.project_id = %s AND pd.doc_id = %s "
        "AND p.id = pd.project_id AND p.owner_user_id = %s",
        (project_id, doc_id, principal.user_id))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(status_code=204)
