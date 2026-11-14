"""The inbox behind the header's bell (spec docs/superpowers/specs/2026-11-06-notifications-design.md, Delivery).

Every route is the signed-in person's own: nobody reads, counts or marks anyone else's notices, and ids that aren't
theirs are ignored without saying so."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..auth import deps
from ..services import notifications

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)


@router.get("/unread")
async def unread(principal: deps.Principal = Depends(deps.get_current_user),
                 conn=Depends(deps.get_conn, scope="function")) -> dict:
    """The bell: unread rows as the list shows them (a group counts once), and how many need action."""
    return await notifications.unread_counts(conn, principal.user_id)


@router.get("")
async def inbox(offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
                principal: deps.Principal = Depends(deps.get_current_user),
                conn=Depends(deps.get_conn, scope="function")) -> dict:
    """A page of rows (groups and single notices) in inbox order."""
    return await notifications.inbox(conn, principal.user_id, offset=offset, limit=limit)


@router.post("/read")
async def read(body: ReadIn, principal: deps.Principal = Depends(deps.get_current_user),
               conn=Depends(deps.get_conn, scope="function")) -> dict:
    """Mark notices read: one, or every member of a group."""
    return {"changed": await notifications.mark_read(conn, principal.user_id, body.ids)}


@router.post("/read-all")
async def read_all(principal: deps.Principal = Depends(deps.get_current_user),
                   conn=Depends(deps.get_conn, scope="function")) -> dict:
    return {"changed": await notifications.mark_all_read(conn, principal.user_id)}
