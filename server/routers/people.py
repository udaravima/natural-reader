"""/v1/users/lookup — find a person to add to a project or share with (A0 §4, §9.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import deps
from ..services import people

router = APIRouter(prefix="/v1/users", tags=["people"])


@router.get("/lookup")
async def lookup_people(q: str = Query("", max_length=200),
                        principal: deps.Principal = Depends(deps.require_capability("reader")),
                        conn=Depends(deps.get_conn)):
    return await people.lookup(conn, people.load_directory_config(), principal.user_id,
                               principal.email, q)
