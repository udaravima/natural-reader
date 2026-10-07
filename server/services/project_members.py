"""Project membership changes (A0 §2, §3.3, §5).

Every write first takes the project's row lock (via require_project_role
lock=True) and reads the caller's role after it, so two writes on one
project run one at a time: two Owners demoting each other at once can't
leave a project with no Owner. A Maintainer manages Readers, Contributors
and Maintainers; Owner rows (adding, changing, removing an Owner, or making
someone one) need an Owner — or an admin, recorded as added_via='admin'."""
from __future__ import annotations

import logging
import uuid

from ..auth.authz import insufficient_role, project_role, require_project_role, role_at_least
from ..http_errors import refusal
from . import people, project_events

logger = logging.getLogger(__name__)

_ROLE_ORDER_SQL = "array_position(ARRAY['owner','maintainer','contributor','reader'], m.role)"


def _member(r, show_email: bool) -> dict:
    out = {
        "user_id": str(r[0]),
        "name": people.person_label(first_name=r[1], last_name=r[2], display_name=r[3],
                                    username=r[4], email=r[5], show_email=show_email),
        "username": people.shown_username(r[4], show_email),
        "status": r[6], "role": r[7], "added_at": r[8], "added_via": r[9],
        "added_by": people.person_from(r[10:16], show_email),
    }
    if show_email:
        out["email"] = r[5]
    return out


async def list_members(conn, project_id, *, show_email: bool,
                       user_id: str | None = None) -> list[dict]:
    cur = await conn.execute(
        f"SELECT {people.person_cols('u')}, u.status, m.role, m.added_at, m.added_via, "
        f"{people.person_cols('ab')} "
        "FROM project_members m JOIN users u ON u.id = m.user_id "
        "LEFT JOIN users ab ON ab.id = m.added_by "
        "WHERE m.project_id = %s AND (%s::uuid IS NULL OR m.user_id = %s::uuid) "
        f"ORDER BY {_ROLE_ORDER_SQL}, "
        "lower(coalesce(u.first_name, u.display_name, u.username, u.email)), u.id",
        (project_id, user_id, user_id))
    return [_member(r, show_email) for r in await cur.fetchall()]


def _authority(access, needed: str) -> str:
    """'member' when the caller's own role suffices; 'admin' when only the
    admin capability does; otherwise 403 naming the role needed."""
    if role_at_least(access.role, needed):
        return "member"
    if access.is_admin:
        return "admin"
    raise insufficient_role(needed)


async def _other_owners(conn, project_id, user_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM project_members WHERE project_id = %s AND role = 'owner' "
        "AND user_id <> %s", (project_id, user_id))
    return (await cur.fetchone())[0]


def _last_owner(project_id, user_id: str):
    logger.warning("last-owner guard: project %s, member %s", project_id, user_id)
    return refusal(409, "last_owner", "A project must keep at least one Owner.")


def _same_user(user_id: str, other: str) -> bool:
    try:
        return str(uuid.UUID(user_id)) == other
    except ValueError:
        return False


async def put_member(conn, principal, project_id, user_id: str, role: str, *,
                     show_email: bool) -> dict:
    access = await require_project_role(conn, principal, project_id, "maintainer", lock=True)
    await people.assert_addable(conn, user_id)
    user_id = str(uuid.UUID(user_id))
    current = await project_role(conn, user_id, project_id)
    via = _authority(access, "owner" if "owner" in (role, current) else "maintainer")
    if current != role:
        if current == "owner" and await _other_owners(conn, project_id, user_id) == 0:
            raise _last_owner(project_id, user_id)
        if current is None:
            await conn.execute(
                "INSERT INTO project_members (project_id, user_id, role, added_by, added_via) "
                "VALUES (%s,%s,%s,%s,%s)", (project_id, user_id, role, principal.user_id, via))
            await project_events.record(conn, project_id, principal.user_id, "member.added",
                                        subject_user_id=user_id,
                                        details={"role": role, "via": via})
            if via == "admin" and user_id == principal.user_id:
                logger.warning("admin %s added themselves to project %s as %s",
                               principal.user_id, project_id, role)
        else:
            await conn.execute(
                "UPDATE project_members SET role = %s WHERE project_id = %s AND user_id = %s",
                (role, project_id, user_id))
            await project_events.record(conn, project_id, principal.user_id,
                                        "member.role_changed", subject_user_id=user_id,
                                        details={"from": current, "to": role})
    return (await list_members(conn, project_id, show_email=show_email, user_id=user_id))[0]


async def remove_member(conn, principal, project_id, user_id: str) -> None:
    leaving = _same_user(user_id, principal.user_id)
    if leaving:
        access = await require_project_role(conn, principal, project_id, None, lock=True)
        if access.role is None:  # an admin who isn't a member
            raise refusal(404, "not_found", "You aren't a member of this project.")
        target, current = principal.user_id, access.role
    else:
        access = await require_project_role(conn, principal, project_id, "maintainer", lock=True)
        try:
            target = str(uuid.UUID(user_id))
        except ValueError:
            raise refusal(404, "not_found", "Member not found")
        current = await project_role(conn, target, project_id)
        if current is None:
            raise refusal(404, "not_found", "Member not found")
        _authority(access, "owner" if current == "owner" else "maintainer")
    if current == "owner" and await _other_owners(conn, project_id, target) == 0:
        raise _last_owner(project_id, target)
    await conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                       (project_id, target))
    await project_events.record(conn, project_id, principal.user_id,
                                "member.left" if leaving else "member.removed",
                                subject_user_id=None if leaving else target,
                                details={"role": current})
