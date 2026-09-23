"""
FastAPI application factory with CORS and router setup.
"""
import logging
import os
import secrets

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .appconfig import cors_allow_credentials, parse_cors_origins
from .auth.config import load_auth_config, startup_guard
from .db import close_db, init_db
from .logging_config import configure_logging
from .endpoints import router as tts_router
from .routers.admin import router as admin_router
from .routers.auth import router as auth_router
from .routers.chat_sessions import router as chat_sessions_router
from .routers.docs import PDF_STORAGE_DIR, router as docs_router
from .routers.inference import router as inference_router
from .routers.inference import start_client as start_inference, stop_client as stop_inference
from .routers.projects import router as projects_router
from .routers.tools import router as tools_router
from .services.embeddings import start_client as start_embeddings, stop_client as stop_embeddings
from .services.web_search import start_client as start_web_search, stop_client as stop_web_search

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # Idempotent — also covers uvicorn worker subprocesses, which re-import the
    # app module rather than going through run.py's __main__.
    configure_logging()
    app = FastAPI()

    # CORS origins come from FRONTEND_ORIGIN (comma-separated). Default stays
    # permissive so the content-script extension keeps working; credentials are
    # enabled only when origins are pinned, avoiding the SEC-3 wildcard footgun.
    cors_origins = parse_cors_origins(os.environ.get("FRONTEND_ORIGIN"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_allow_credentials(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Signed cookie holding the transient OIDC flow state (Authlib uses
    # request.session for state/nonce/PKCE). Separate from the app session.
    _auth_cfg = load_auth_config(os.environ)
    session_secret = _auth_cfg.session_secret
    if not session_secret:
        # No configured secret: use a per-process random value rather than a
        # known constant, so cookies can never be forged with a shared key. This
        # is a loopback-dev convenience only — startup_guard() refuses to boot a
        # non-loopback bind without a real SESSION_SECRET. The ephemeral secret
        # won't survive a restart and won't validate across multiple workers.
        session_secret = secrets.token_urlsafe(48)
        logger.warning(
            "SESSION_SECRET is not set — using an ephemeral per-process secret. "
            "Set SESSION_SECRET for any real deployment (and whenever WORKERS>1)."
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=_auth_cfg.cookie_secure,
    )

    app.include_router(tts_router)
    app.include_router(chat_sessions_router)
    app.include_router(docs_router)
    app.include_router(tools_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(projects_router)
    app.include_router(inference_router)

    @app.on_event("startup")
    async def _startup() -> None:
        # Fail fast if the auth bypass is on with a non-loopback bind.
        startup_guard(
            auth_enabled=_auth_cfg.enabled,
            bind_host=os.environ.get("HOST", "127.0.0.1"),
            session_secret=_auth_cfg.session_secret,
        )
        # init_db retries on its own and never raises — the app comes up even
        # when Postgres is down so TTS keeps serving. Chat routes will return
        # 503 until the DB is reachable.
        ok = await init_db()
        if not ok:
            logger.warning("Postgres is offline; chat persistence is disabled")
        await start_embeddings()
        await start_web_search()
        await start_inference()
        # Make sure the PDF-retention directory exists before the first upload
        # hits — Path.mkdir in the route is a fallback, not the primary owner.
        try:
            PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create PDF storage dir %s: %s", PDF_STORAGE_DIR, e)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await stop_inference()
        await stop_embeddings()
        await stop_web_search()
        await close_db()

    return app


app = create_app()
