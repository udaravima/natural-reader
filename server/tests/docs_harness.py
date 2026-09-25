"""App + pool shim for doc-route tests that exercise background jobs.

docs.py and doc_pipeline.py reach the DB through their own imported
`get_pool`; both are patched to hand back the test's transactional conn.
httpx's ASGITransport awaits the whole ASGI call, so FastAPI background tasks
have finished by the time a request returns — pipeline tests are deterministic.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import docs as docs_router
from server.services import doc_pipeline
from server.services.embeddings import EMBEDDING_DIM


class _PoolShim:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


async def fake_embed(texts):
    return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]


def build_docs_app(db_conn, monkeypatch, *, storage_dir, embed=None):
    shim = _PoolShim(db_conn)
    for mod in (docs_router, doc_pipeline):
        monkeypatch.setattr(mod, "get_pool", lambda: shim)
        monkeypatch.setattr(mod, "is_ready", lambda: True)
    monkeypatch.setattr(doc_pipeline, "embed_batch", embed or fake_embed)
    monkeypatch.setenv("DOC_STORAGE_DIR", str(storage_dir))
    current = {}
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    app.include_router(docs_router.router)

    def as_user(principal):
        current["p"] = principal
        return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    return app, as_user
