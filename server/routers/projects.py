"""/v1/projects — grouping + primary sharing unit. Owner-only writes; listing
returns owned + member-of. Not-owned writes 404 (no existence leak)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps

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
