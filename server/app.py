"""
FastAPI application factory with CORS and router setup.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import close_db, init_db
from .endpoints import router as tts_router
from .routers.chat_sessions import router as chat_sessions_router
from .routers.docs import router as docs_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI()

    # Allow CORS so our React frontend can talk to this server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict this to your frontend URL
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(tts_router)
    app.include_router(chat_sessions_router)
    app.include_router(docs_router)

    @app.on_event("startup")
    async def _startup() -> None:
        # init_db retries on its own and never raises — the app comes up even
        # when Postgres is down so TTS keeps serving. Chat routes will return
        # 503 until the DB is reachable.
        ok = await init_db()
        if not ok:
            logger.warning("Postgres is offline; chat persistence is disabled")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await close_db()

    return app


app = create_app()
