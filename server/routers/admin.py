"""/v1/admin/* — user administration. Admin manages accounts, not their data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import deps, users
from ..services import inference_budget

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class UserPatchIn(BaseModel):
    status: str | None = None
    role: str | None = None
    inference_daily_token_budget: int | None = None


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
    # exclude_unset distinguishes "field absent" (untouched) from "explicit
    # null" (clear back to the deployment default) — required for the budget.
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        if data["status"] not in ("active", "pending", "disabled"):
            raise HTTPException(status_code=422, detail="bad status")
        await users.set_status(conn, user_id, data["status"])
    if "role" in data:
        if data["role"] not in ("admin", "member"):
            raise HTTPException(status_code=422, detail="bad role")
        await users.set_role(conn, user_id, data["role"])
    if "inference_daily_token_budget" in data:
        budget = data["inference_daily_token_budget"]
        if budget is not None and budget < 0:
            raise HTTPException(status_code=422, detail="budget must be >= 0")
        await users.set_inference_budget(conn, user_id, budget)
    updated = await users.get_user(conn, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.get("/inference/usage")
async def inference_usage(
    days: int = 7,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
):
    days = max(1, min(90, days))
    return await inference_budget.admin_usage(conn, days=days)
