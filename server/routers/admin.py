"""/v1/admin/* — user administration. Admin manages accounts, not their data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import deps, users

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class UserPatchIn(BaseModel):
    status: str | None = None
    role: str | None = None


@router.get("/users")
async def list_all(
    _: deps.Principal = Depends(deps.require_admin), conn=Depends(deps.get_conn)
):
    return await users.list_users(conn)


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
):
    if body.status is not None:
        if body.status not in ("active", "pending", "disabled"):
            raise HTTPException(status_code=422, detail="bad status")
        await users.set_status(conn, user_id, body.status)
    if body.role is not None:
        if body.role not in ("admin", "member"):
            raise HTTPException(status_code=422, detail="bad role")
        await users.set_role(conn, user_id, body.role)
    updated = await users.get_user(conn, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated
