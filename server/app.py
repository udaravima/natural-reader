"""
FastAPI application factory with CORS and router setup.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import close_db, init_db
from .logging_config import configure_logging
from .endpoints import router as tts_router
from .routers.chat_sessions import router as chat_sessions_router
from .routers.docs import PDF_STORAGE_DIR, router as docs_router
from .routers.tools import router as tools_router
from .services.embeddings import start_client as start_embeddings, stop_client as stop_embeddings
from .services.web_search import start_client as start_web_search, stop_client as stop_web_search

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # Idempotent — also covers uvicorn worker subprocesses, which re-import the
    # app module rather than going through run.py's __main__.
    configure_logging()
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
    app.include_router(tools_router)

    @app.on_event("startup")
    async def _startup() -> None:
        # init_db retries on its own and never raises — the app comes up even
        # when Postgres is down so TTS keeps serving. Chat routes will return
        # 503 until the DB is reachable.
        ok = await init_db()
        if not ok:
            logger.warning("Postgres is offline; chat persistence is disabled")
        await start_embeddings()
        await start_web_search()
        # Make sure the PDF-retention directory exists before the first upload
        # hits — Path.mkdir in the route is a fallback, not the primary owner.
        try:
            PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create PDF storage dir %s: %s", PDF_STORAGE_DIR, e)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await stop_embeddings()
        await stop_web_search()
        await close_db()

    return app


app = create_app()
