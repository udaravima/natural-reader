"""/v1/projects — projects, their members, documents and activity (A0).
Membership (`project_members.role`, Reader < Contributor < Maintainer <
Owner) is the only source of access. Every route's first check is
`require_project_role`: a non-member gets 404 before any other id is
examined, and a member below the route's role gets 403 insufficient_role.
Project responses carry `can` (authz.can_for), so the UI never re-derives
the rules. Documents (A1 §5): a Contributor who holds an upload entry files
them in; only a Maintainer or Owner removes them."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..audit import audit
from ..auth import deps
from ..auth.authz import (
    assert_holds_upload,
    can_for,
    can_manage_project_docs,
    require_project_role,
    visible_projects_params,
    visible_projects_where,
)
from ..http_errors import refusal
from ..services import doc_content, people, project_events, project_policy
from .docs import DocId

router = APIRouter(prefix="/v1/projects", tags=["projects"])
_reader = deps.require_capability("reader")


def _clean_name(v: str | None) -> str:
    if v is None or not v.strip():
        raise ValueError("name must not be blank")
    return v.strip()


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    owner_user_id: uuid.UUID | None = None  # admins only: create for someone else

    clean_name = field_validator("name")(_clean_name)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

    clean_name = field_validator("name")(_clean_name)


def _show_email() -> bool:
    return people.load_directory_config().show_email


def _project_sql(where: str) -> str:
    """Bind [caller user_id, *where params]."""
    return (
        "SELECT p.id, p.name, p.description, p.created_at, "
        f"{people.person_cols('cb')}, "
        "(SELECT role FROM project_members WHERE project_id = p.id AND user_id = %s), "
        "(SELECT count(*) FROM project_members WHERE project_id = p.id), "
        "(SELECT count(*) FROM project_documents WHERE project_id = p.id AND verified) "
        f"FROM projects p LEFT JOIN users cb ON cb.id = p.created_by WHERE {where}"
    )


def _project(r, *, is_admin: bool, show_email: bool) -> dict:
    my_role = r[10]
    return {"id": str(r[0]), "name": r[1], "description": r[2], "created_at": r[3],
            "created_by": people.person_from(r[4:10], show_email),
            "my_role": my_role, "member_count": r[11], "doc_count": r[12],
            "can": can_for(my_role, is_admin)}


async def _load_project(conn, principal, project_id) -> dict:
    cur = await conn.execute(_project_sql("p.id = %s"), (principal.user_id, project_id))
    return _project(await cur.fetchone(), is_admin=principal.role == "admin",
                    show_email=_show_email())


async def _assert_owner(conn, project_id: str, user_id: str) -> None:
    cur = await conn.execute(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s "
        "AND role = 'owner'", (project_id, user_id))
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("", status_code=201)
async def create_project(body: ProjectIn, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    if body.owner_user_id is not None and principal.role != "admin":
        raise refusal(403, "insufficient_role",
                      "Only admins can create a project for someone else.", required="admin")
    await project_policy.check_can_create(conn, principal, project_policy.load_project_policy())
    owner_id = str(body.owner_user_id) if body.owner_user_id else principal.user_id
    if owner_id != principal.user_id:
        await people.assert_addable(conn, owner_id)
    cur = await conn.execute(
        "INSERT INTO projects (created_by, name, description) VALUES (%s,%s,%s) RETURNING id",
        (principal.user_id, body.name, body.description))
    pid = str((await cur.fetchone())[0])
    via = "member" if owner_id == principal.user_id else "admin"
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id, role, added_by, added_via) "
        "VALUES (%s,%s,'owner',%s,%s)", (pid, owner_id, principal.user_id, via))
    await project_events.record(
        conn, pid, principal.user_id, "project.created",
        subject_user_id=None if via == "member" else owner_id, details={"name": body.name})
    return await _load_project(conn, principal, pid)


@router.get("")
async def list_projects(principal: deps.Principal = Depends(_reader),
                        conn=Depends(deps.get_conn)):
    """Projects the caller is a member of, newest first. Admins too: every
    project is listed under /v1/admin/projects."""
    cur = await conn.execute(
        _project_sql(visible_projects_where("p")) + " ORDER BY p.created_at DESC",
        [principal.user_id, *visible_projects_params(principal.user_id)])
    show_email = _show_email()
    return [_project(r, is_admin=principal.role == "admin", show_email=show_email)
            for r in await cur.fetchall()]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                      conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None)
    return await _load_project(conn, principal, project_id)


@router.patch("/{project_id}")
async def patch_project(project_id: uuid.UUID, body: ProjectPatch,
                        principal: deps.Principal = Depends(_reader),
                        conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "maintainer")
    data = body.model_dump(exclude_unset=True)
    cur = await conn.execute("SELECT name, description FROM projects WHERE id = %s", (project_id,))
    old_name, old_description = await cur.fetchone()
    if "name" in data and data["name"] != old_name:
        await conn.execute("UPDATE projects SET name = %s WHERE id = %s", (data["name"], project_id))
        await project_events.record(conn, project_id, principal.user_id, "project.renamed",
                                    details={"from": old_name, "to": data["name"]})
    if "description" in data and data["description"] != old_description:
        await conn.execute("UPDATE projects SET description = %s WHERE id = %s",
                           (data["description"], project_id))
        await project_events.record(conn, project_id, principal.user_id, "project.described")
    return await _load_project(conn, principal, project_id)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "owner", lock=True)
    # Sorted, like admin delete_user's GC: row locks in one order can't deadlock.
    cur = await conn.execute(
        "SELECT doc_id FROM project_documents WHERE project_id = %s ORDER BY doc_id",
        (project_id,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    audit("project.deleted", project=project_id, by=principal.user_id)
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
                "INSERT INTO project_members (project_id, user_id, role, added_by) "
                "VALUES (%s,%s,'contributor',%s) ON CONFLICT DO NOTHING",
                (project_id, user_id, principal.user_id))
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
