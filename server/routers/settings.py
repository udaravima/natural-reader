"""/v1/settings/* — what every signed-in user's browser needs to know about
the deployment's preferences (services/user_defaults.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import deps
from ..services import docling_convert, docling_limits, helper, user_defaults

router = APIRouter(prefix="/v1/settings", tags=["settings"])


@router.get("/defaults")
async def get_defaults(
    _: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn, scope="function"),
):
    return await user_defaults.effective(conn)


@router.get("/docling")
async def get_docling(
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn, scope="function"),
):
    """What the convert dialog may offer this person (the admin's Docling limits, services/docling_limits.py)."""
    limits = await docling_limits.load_limits(conn)
    return docling_limits.user_view(limits, is_admin=principal.role == "admin",
                                    capabilities=principal.capabilities, enabled=docling_convert.is_enabled())


@router.get("/helper")
async def get_helper(
    _: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn, scope="function"),
):
    """Whether a helper model is set up (helper spec §5.4): the screens offer Generate and the upload's "Write tags
    and descriptions" tick only then. The model's name stays the admin's business."""
    return {"on": (await helper.load_config(conn)).on}
