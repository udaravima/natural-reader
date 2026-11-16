"""What the app is called here (services/app_identity.py). Answers before sign-in: the sign-in screen shows it."""
from fastapi import APIRouter

from ..services import app_identity

router = APIRouter()


@router.get("/v1/app")
async def app_info() -> dict:
    identity = app_identity.current()
    return {"name": identity.name, "tagline": identity.tagline, "copyright": identity.copyright}
