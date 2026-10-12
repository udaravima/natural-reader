"""docs_harness plus the project routers, for version uploads: the upload
route stores bytes through docs.py's pool (shimmed to the test conn) and the
other routes use deps.get_conn (overridden to the same conn)."""
from __future__ import annotations

from server.auth import deps
from server.routers import project_documents as pd_router
from server.routers import projects as projects_router
from server.tests.docs_harness import build_docs_app


def build_versions_app(db_conn, monkeypatch, *, storage_dir, embed=None):
    app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=storage_dir, embed=embed)

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(projects_router.router)
    app.include_router(pd_router.router)
    return app, as_user
