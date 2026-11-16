"""The profiles a person can pick for a chat (profile library spec, "For a person"): the ones an admin left on,
by name and description. Never their text, which is the deployer's instruction to the assistant."""
from fastapi import APIRouter, Depends

from ..auth import deps
from ..services import profile_library

router = APIRouter()


@router.get("/v1/chat/profiles")
async def chat_profiles(
    _: deps.Principal = Depends(deps.require_capability("chat")),
    conn=Depends(deps.get_conn, scope="function"),
):
    rows = await profile_library.list_profiles(conn, enabled_only=True)
    return {"profiles": [{"id": r.id, "name": r.name, "description": r.description, "isDefault": r.is_default}
                         for r in rows]}
