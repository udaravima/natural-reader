"""A0 §5: the project-role seam. One place decides membership, role order,
the 404/403 split and the `can` object the UI reads."""
import pytest
from fastapi import HTTPException

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


def _p(uid, *, admin=False):
    return deps.Principal(user_id=uid, email="x@x.io", role="admin" if admin else "member",
                          capabilities=frozenset({"reader"}))


@pytest.mark.parametrize("role,minimum,expected", [
    (None, "reader", False), ("reader", "reader", True), ("reader", "contributor", False),
    ("contributor", "contributor", True), ("contributor", "maintainer", False),
    ("maintainer", "maintainer", True), ("maintainer", "owner", False), ("owner", "maintainer", True),
])
def test_role_at_least(role, minimum, expected):
    assert authz.role_at_least(role, minimum) is expected


@pytest.mark.parametrize("role,is_admin,expected", [
    (None, False, dict(edit=False, manage_members=False, manage_owners=False, file_docs=False,
                       remove_docs=False, delete=False, leave=False)),
    ("reader", False, dict(edit=False, manage_members=False, manage_owners=False, file_docs=False,
                           remove_docs=False, delete=False, leave=True)),
    ("contributor", False, dict(edit=False, manage_members=False, manage_owners=False, file_docs=True,
                                remove_docs=False, delete=False, leave=True)),
    ("maintainer", False, dict(edit=True, manage_members=True, manage_owners=False, file_docs=True,
                               remove_docs=True, delete=False, leave=True)),
    ("owner", False, dict(edit=True, manage_members=True, manage_owners=True, file_docs=True,
                          remove_docs=True, delete=True, leave=True)),
    # Admins manage any project but file/remove documents only through a member role (§5).
    (None, True, dict(edit=True, manage_members=True, manage_owners=True, file_docs=False,
                      remove_docs=False, delete=True, leave=False)),
    ("reader", True, dict(edit=True, manage_members=True, manage_owners=True, file_docs=False,
                          remove_docs=False, delete=True, leave=True)),
])
def test_can_for(role, is_admin, expected):
    assert authz.can_for(role, is_admin) == expected


async def test_project_role_reads_membership(db_conn):
    owner, other = await _user(db_conn, "o"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner)
    assert await authz.project_role(db_conn, owner, pid) == "owner"
    assert await authz.project_role(db_conn, other, pid) is None
    assert await authz.project_role(db_conn, owner, pid, lock=True) == "owner"


async def test_require_project_role_404_for_non_members_and_missing_projects(db_conn):
    owner, other = await _user(db_conn, "o"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner)
    for project_id in (pid, "00000000-0000-0000-0000-00000000dead"):
        with pytest.raises(HTTPException) as e:
            await authz.require_project_role(db_conn, _p(other), project_id, None)
        assert e.value.status_code == 404
        assert e.value.detail == {"error": "not_found", "message": authz.PROJECT_NOT_FOUND}


async def test_require_project_role_403_names_the_required_role(db_conn):
    owner, reader = await _user(db_conn, "o"), await _user(db_conn, "r")
    pid = await seed.make_project(db_conn, owner, members=[(reader, "reader")])
    with pytest.raises(HTTPException) as e:
        await authz.require_project_role(db_conn, _p(reader), pid, "maintainer")
    assert e.value.status_code == 403
    assert e.value.detail["error"] == "insufficient_role"
    assert e.value.detail["required"] == "maintainer"
    assert e.value.detail["message"] == "Only Maintainers or Owners can do that."
    access = await authz.require_project_role(db_conn, _p(reader), pid, "reader")
    assert access == authz.ProjectAccess(role="reader", is_admin=False)


async def test_admins_pass_only_when_admin_ok(db_conn):
    owner, admin = await _user(db_conn, "o"), await _user(db_conn, "a")
    pid = await seed.make_project(db_conn, owner)
    access = await authz.require_project_role(db_conn, _p(admin, admin=True), pid, "owner")
    assert access == authz.ProjectAccess(role=None, is_admin=True)
    with pytest.raises(HTTPException) as e:  # documents: admins act only as members (§5)
        await authz.require_project_role(db_conn, _p(admin, admin=True), pid, "contributor",
                                         admin_ok=False)
    assert e.value.status_code == 404


async def test_can_manage_project_docs_is_maintainer_or_above(db_conn):
    owner, m, c = await _user(db_conn, "o"), await _user(db_conn, "m"), await _user(db_conn, "c")
    pid = await seed.make_project(db_conn, owner, members=[(m, "maintainer"), (c, "contributor")])
    assert await authz.can_manage_project_docs(db_conn, owner, pid)
    assert await authz.can_manage_project_docs(db_conn, m, pid)
    assert not await authz.can_manage_project_docs(db_conn, c, pid)
