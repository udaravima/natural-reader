"""/v1/settings/* — what every signed-in user's browser needs to know about
the deployment's preferences (services/user_defaults.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import deps
from ..services import user_defaults

router = APIRouter(prefix="/v1/settings", tags=["settings"])


@router.get("/defaults")
async def get_defaults(
    _: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn, scope="function"),
):
    return await user_defaults.effective(conn)
