"""C2.1 Task 13: the deployment's chat options (so far, how web search asks), edited by an admin.

Which value applies, first match wins: the admin's choice (an `app_settings` row), the CHAT_WEB_SEARCH
environment variable, then "ask". A reset ({"web_search": null}) deletes the row, as the assistant profile's
empty text does."""
from __future__ import annotations

import logging
import re

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router
from server.services import chat_options, web_search as web_search_service
from server.services.chat_options import WEB_MODES, ChatOptions, load_chat_options, save_chat_options


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    chat_options._warned.clear()      # an invalid value is warned about once per process: each test starts afresh


async def _user_id(db_conn, sub="s1", email="a@x.io"):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=email)
    return str(u["id"])


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _principal(db_conn, sub, email, role):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=email)
    return deps.Principal(user_id=str(u["id"]), email=u["email"], role=role)


async def test_admin_choice_beats_env_beats_default(db_conn):
    assert WEB_MODES == ("ask", "auto", "off")
    assert await load_chat_options(db_conn, {}) == ChatOptions("ask", "default")
    assert await load_chat_options(db_conn, {"CHAT_WEB_SEARCH": "auto"}) == ChatOptions("auto", "env")
    by = await _user_id(db_conn)
    saved = await save_chat_options(db_conn, "off", updated_by=by)
    assert saved == ChatOptions("off", "admin")
    # The admin's row wins over the environment, whatever the environment says.
    assert await load_chat_options(db_conn, {"CHAT_WEB_SEARCH": "auto"}) == ChatOptions("off", "admin")
    assert await load_chat_options(db_conn, {}) == ChatOptions("off", "admin")


async def test_the_environment_value_is_trimmed_and_case_folded(db_conn):
    assert await load_chat_options(db_conn, {"CHAT_WEB_SEARCH": " Auto "}) == ChatOptions("auto", "env")


async def test_invalid_env_value_falls_back_to_ask(db_conn, caplog):
    with caplog.at_level(logging.WARNING):
        got = await load_chat_options(db_conn, {"CHAT_WEB_SEARCH": "sometimes"})
    assert got == ChatOptions("ask", "default")
    assert "CHAT_WEB_SEARCH" in caplog.text and "ask|auto|off" in caplog.text


@pytest.mark.parametrize("stored", ['{"web_search": "maybe"}', '{"other": 1}', '["auto"]', '"auto"', 'null'])
async def test_an_unusable_stored_value_is_ignored_like_an_invalid_env_value(db_conn, caplog, stored):
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('chat_options', %s::jsonb)", (stored,))
    with caplog.at_level(logging.WARNING):
        assert await load_chat_options(db_conn, {"CHAT_WEB_SEARCH": "auto"}) == ChatOptions("auto", "env")
    assert "chat_options" in caplog.text


async def test_reset_returns_to_env_and_then_to_the_default(db_conn):
    by = await _user_id(db_conn)
    await save_chat_options(db_conn, "off", updated_by=by)
    env = {"CHAT_WEB_SEARCH": "auto"}
    assert await save_chat_options(db_conn, None, updated_by=by, env=env) == ChatOptions("auto", "env")
    cur = await db_conn.execute("SELECT count(*) FROM app_settings WHERE key = 'chat_options'")
    assert (await cur.fetchone())[0] == 0                       # the row is gone, not set to null
    assert await save_chat_options(db_conn, None, updated_by=by, env={}) == ChatOptions("ask", "default")


async def test_a_mode_outside_the_three_is_refused_by_the_service(db_conn):
    with pytest.raises(ValueError):
        await save_chat_options(db_conn, "sometimes", updated_by=await _user_id(db_conn))


# ---------- the admin routes ----------

async def test_put_requires_admin(db_conn):
    member = await _principal(db_conn, "s2", "b@x.io", "member")
    async with _client(_app(db_conn, member)) as client:
        assert (await client.get("/v1/admin/chat-options")).status_code == 403
        assert (await client.put("/v1/admin/chat-options", json={"web_search": "auto"})).status_code == 403
    assert await load_chat_options(db_conn, {}) == ChatOptions("ask", "default")   # nothing was written


async def test_get_and_put_return_the_same_view(db_conn):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        first = (await client.get("/v1/admin/chat-options")).json()
        assert first == {"web_search": "ask", "source": "default", "searxng": True}
        put = await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
        assert put.status_code == 200
        assert put.json() == {"web_search": "auto", "source": "admin", "searxng": True}
        assert (await client.get("/v1/admin/chat-options")).json() == put.json()


async def test_put_null_resets_to_env_then_default(db_conn, monkeypatch):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    monkeypatch.setenv("CHAT_WEB_SEARCH", "off")
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
        reset = await client.put("/v1/admin/chat-options", json={"web_search": None})
        assert reset.status_code == 200
        assert reset.json() == {"web_search": "off", "source": "env", "searxng": True}
        monkeypatch.delenv("CHAT_WEB_SEARCH")
        assert (await client.put("/v1/admin/chat-options", json={"web_search": None})).json() == {
            "web_search": "ask", "source": "default", "searxng": True}


@pytest.mark.parametrize("body", [{"web_search": "sometimes"}, {"web_search": ""}, {"web_search": 1}, {},
                                  {"web_search": "ask", "extra": 1}])
async def test_a_bad_body_is_a_422_and_writes_nothing(db_conn, body):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        assert (await client.put("/v1/admin/chat-options", json=body)).status_code == 422
    assert await load_chat_options(db_conn, {}) == ChatOptions("ask", "default")


async def test_put_writes_an_audit_line(db_conn, caplog):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    with caplog.at_level(logging.INFO, logger="server.audit"):
        async with _client(_app(db_conn, admin)) as client:
            await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
            await client.put("/v1/admin/chat-options", json={"web_search": None})
    lines = [r.getMessage() for r in caplog.records if r.name == "server.audit"]
    # Ids and modes only: who, the mode before, the mode after (None on a reset).
    assert lines == [f"chat_options.web_search by={admin.user_id} old=ask new=auto",
                     f"chat_options.web_search by={admin.user_id} old=auto new=None"]


async def test_view_reports_searxng_missing(db_conn, monkeypatch):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    monkeypatch.setattr(web_search_service, "SEARXNG_URL", "")
    async with _client(_app(db_conn, admin)) as client:
        assert (await client.get("/v1/admin/chat-options")).json()["searxng"] is False
        # The setting itself is still kept: the control is shown disabled, not removed.
        put = await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
        assert put.json() == {"web_search": "auto", "source": "admin", "searxng": False}


async def test_an_anonymous_request_gets_401_on_get_and_put(db_conn):
    """No principal at all (not a member without the admin role: that is the 403 above)."""
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.include_router(admin_router.router)
    async with _client(app) as client:
        assert (await client.get("/v1/admin/chat-options")).status_code == 401
        assert (await client.put("/v1/admin/chat-options", json={"web_search": "off"})).status_code == 401
    assert await load_chat_options(db_conn, {}) == ChatOptions("ask", "default")


# ---------- the assistant preview follows the mode (C2.1 Task 19) ----------

def _names(preview: str) -> set[str]:
    """The chat tools the preview names. \\b keeps "web_search" from matching inside "offer_web_search"."""
    every = ("search_documents", "read_document_pages", "extract_from_documents", "document_outline",
             "web_search", "read_web_pages", "offer_web_search")
    return {name for name in every if re.search(rf"\b{name}\b", preview)}


_DOCUMENT_TOOLS = {"search_documents", "read_document_pages", "extract_from_documents"}


@pytest.mark.parametrize("mode, web_tools", [("auto", {"web_search", "read_web_pages"}),
                                             ("ask", {"offer_web_search"}),
                                             ("off", set())])
async def test_the_assistant_preview_names_the_web_tools_of_the_mode(db_conn, mode, web_tools):
    """The preview is what a real document-scope turn would get: the document tools (document_outline is for
    project chats), and the web tools of the deployment's mode. Never web_search beside the offer sentence."""
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"web_search": mode})
        preview = (await client.get("/v1/admin/assistant")).json()["preview"]
        assert _names(preview) == _DOCUMENT_TOOLS | web_tools


@pytest.mark.parametrize("mode", ["auto", "ask", "off"])
async def test_the_assistant_preview_has_no_web_tool_without_searxng(db_conn, monkeypatch, mode):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    monkeypatch.setattr(web_search_service, "SEARXNG_URL", "")
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"web_search": mode})
        preview = (await client.get("/v1/admin/assistant")).json()["preview"]
    assert _names(preview) == _DOCUMENT_TOOLS


async def test_the_assistant_preview_follows_the_environment_when_the_admin_has_not_chosen(db_conn, monkeypatch):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")
    async with _client(_app(db_conn, admin)) as client:
        assert "web_search" in _names((await client.get("/v1/admin/assistant")).json()["preview"])
        monkeypatch.delenv("CHAT_WEB_SEARCH")
        assert _names((await client.get("/v1/admin/assistant")).json()["preview"]) \
            == _DOCUMENT_TOOLS | {"offer_web_search"}
