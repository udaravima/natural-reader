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
from server.services.chat_options import (WEB_MODES, ChatOptions, load_chat_options, save_chat_options,
                                          save_voice_input)
from server.speech import service as speech_service


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    monkeypatch.delenv("CHAT_VOICE_INPUT", raising=False)
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


@pytest.mark.parametrize("stored", ['{"web_search": "maybe"}', '["auto"]', '"auto"', 'null'])
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
        assert first == {"web_search": "ask", "source": "default", "searxng": True,
                         "voice_input": False, "voice_source": "default"}
        put = await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
        assert put.status_code == 200
        assert put.json() == {"web_search": "auto", "source": "admin", "searxng": True,
                              "voice_input": False, "voice_source": "default"}
        assert (await client.get("/v1/admin/chat-options")).json() == put.json()


async def test_put_null_resets_to_env_then_default(db_conn, monkeypatch):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    monkeypatch.setenv("CHAT_WEB_SEARCH", "off")
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
        reset = await client.put("/v1/admin/chat-options", json={"web_search": None})
        assert reset.status_code == 200
        assert reset.json() == {"web_search": "off", "source": "env", "searxng": True,
                                "voice_input": False, "voice_source": "default"}
        monkeypatch.delenv("CHAT_WEB_SEARCH")
        assert (await client.put("/v1/admin/chat-options", json={"web_search": None})).json() == {
            "web_search": "ask", "source": "default", "searxng": True,
            "voice_input": False, "voice_source": "default"}


@pytest.mark.parametrize("body", [{"web_search": "sometimes"}, {"web_search": ""}, {"web_search": 1}, {},
                                  {"web_search": "ask", "extra": 1}, {"voice_input": "yes"}, {"voice_input": 1},
                                  {"voice_input": True, "web_search": "sometimes"}])
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
        assert put.json() == {"web_search": "auto", "source": "admin", "searxng": False,
                              "voice_input": False, "voice_source": "default"}


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


# ---------- voice input: a second key in the same row (Task 6) ----------

async def _row(db_conn):
    cur = await db_conn.execute("SELECT value FROM app_settings WHERE key = 'chat_options'")
    row = await cur.fetchone()
    return row[0] if row else None


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_voice_comes_from_the_environment_then_the_default(db_conn):
    assert await load_chat_options(db_conn, {}) == ChatOptions("ask", "default", False, "default")
    got = await load_chat_options(db_conn, {"CHAT_VOICE_INPUT": " ON "})
    assert (got.voice_input, got.voice_source) == (True, "env")
    got = await load_chat_options(db_conn, {"CHAT_VOICE_INPUT": "off"})
    assert (got.voice_input, got.voice_source) == (False, "env")


async def test_an_invalid_voice_env_value_warns_once_and_means_default(db_conn, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            got = await load_chat_options(db_conn, {"CHAT_VOICE_INPUT": "maybe"})
    assert (got.voice_input, got.voice_source) == (False, "default")
    assert len([r for r in _warnings(caplog) if "CHAT_VOICE_INPUT" in r.getMessage()]) == 1


async def test_from_env_and_the_old_constructor_still_work_unchanged():
    assert chat_options.from_env({}) == ChatOptions("ask", "default")
    assert chat_options.from_env({"CHAT_WEB_SEARCH": "auto"}) == ChatOptions("auto", "env")
    assert ChatOptions("off", "admin").voice_input is False


async def test_a_voice_only_row_loads_without_a_warning(db_conn, caplog):
    by = await _user_id(db_conn)
    await save_voice_input(db_conn, True, updated_by=by)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        got = await load_chat_options(db_conn, {"CHAT_WEB_SEARCH": "auto"})
    assert got == ChatOptions("auto", "env", True, "admin")      # web falls through to the environment
    assert _warnings(caplog) == []


async def test_a_web_only_row_loads_without_a_warning(db_conn, caplog):
    by = await _user_id(db_conn)
    await save_chat_options(db_conn, "off", updated_by=by)
    with caplog.at_level(logging.WARNING):
        got = await load_chat_options(db_conn, {"CHAT_VOICE_INPUT": "on"})
    assert got == ChatOptions("off", "admin", True, "env")
    assert _warnings(caplog) == []


async def test_an_unusable_voice_value_is_warned_about_and_ignored(db_conn, caplog):
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('chat_options', %s::jsonb)",
                          ('{"voice_input": "yes", "web_search": "off"}',))
    with caplog.at_level(logging.WARNING):
        got = await load_chat_options(db_conn, {"CHAT_VOICE_INPUT": "on"})
    assert got == ChatOptions("off", "admin", True, "env")
    assert any("voice_input" in r.getMessage() for r in _warnings(caplog))


async def test_save_voice_input_keeps_web_search_and_the_other_way_round(db_conn):
    by = await _user_id(db_conn)
    await save_chat_options(db_conn, "auto", updated_by=by)
    got = await save_voice_input(db_conn, True, updated_by=by)
    assert got == ChatOptions("auto", "admin", True, "admin")
    assert await _row(db_conn) == {"web_search": "auto", "voice_input": True}
    got = await save_chat_options(db_conn, "off", updated_by=by)          # the other order: voice survives
    assert got == ChatOptions("off", "admin", True, "admin")
    assert await _row(db_conn) == {"web_search": "off", "voice_input": True}


async def test_voice_first_then_web_search_keeps_both(db_conn):
    by = await _user_id(db_conn)
    await save_voice_input(db_conn, False, updated_by=by)
    await save_chat_options(db_conn, "auto", updated_by=by)
    assert await _row(db_conn) == {"web_search": "auto", "voice_input": False}


async def test_voice_false_is_an_admin_choice_that_beats_the_environment(db_conn):
    by = await _user_id(db_conn)
    got = await save_voice_input(db_conn, False, updated_by=by, env={"CHAT_VOICE_INPUT": "on"})
    assert (got.voice_input, got.voice_source) == (False, "admin")


async def test_resetting_voice_removes_only_that_key(db_conn):
    by = await _user_id(db_conn)
    await save_chat_options(db_conn, "auto", updated_by=by)
    await save_voice_input(db_conn, True, updated_by=by)
    got = await save_voice_input(db_conn, None, updated_by=by, env={"CHAT_VOICE_INPUT": "on"})
    assert (got.voice_input, got.voice_source, got.web_search) == (True, "env", "auto")
    assert await _row(db_conn) == {"web_search": "auto"}


async def test_resetting_web_search_removes_only_that_key(db_conn):
    by = await _user_id(db_conn)
    await save_chat_options(db_conn, "auto", updated_by=by)
    await save_voice_input(db_conn, True, updated_by=by)
    got = await save_chat_options(db_conn, None, updated_by=by)
    assert (got.web_search, got.source, got.voice_input, got.voice_source) == ("ask", "default", True, "admin")
    assert await _row(db_conn) == {"voice_input": True}


@pytest.mark.parametrize("first", ["web", "voice"])
async def test_resetting_both_in_either_order_deletes_the_row(db_conn, first):
    by = await _user_id(db_conn)
    await save_chat_options(db_conn, "auto", updated_by=by)
    await save_voice_input(db_conn, True, updated_by=by)
    resets = [lambda: save_chat_options(db_conn, None, updated_by=by),
              lambda: save_voice_input(db_conn, None, updated_by=by)]
    for reset in (resets if first == "web" else resets[::-1]):
        await reset()
    assert await _row(db_conn) is None


async def test_resetting_voice_with_no_row_is_a_no_op(db_conn):
    got = await save_voice_input(db_conn, None, updated_by=await _user_id(db_conn), env={})
    assert got == ChatOptions("ask", "default", False, "default")
    assert await _row(db_conn) is None


async def test_resetting_a_garbage_row_deletes_it(db_conn):
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('chat_options', '[1]'::jsonb)")
    await save_voice_input(db_conn, None, updated_by=await _user_id(db_conn))
    assert await _row(db_conn) is None


async def test_saving_voice_over_a_garbage_row_replaces_it(db_conn):
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('chat_options', '[1]'::jsonb)")
    await save_voice_input(db_conn, True, updated_by=await _user_id(db_conn))
    assert await _row(db_conn) == {"voice_input": True}


async def test_a_non_boolean_voice_value_is_refused_by_the_service(db_conn):
    with pytest.raises(ValueError):
        await save_voice_input(db_conn, "yes", updated_by=await _user_id(db_conn))


async def test_voice_audit_line_has_old_and_new(db_conn, caplog):
    by = await _user_id(db_conn)
    with caplog.at_level(logging.INFO, logger="server.audit"):
        await save_voice_input(db_conn, True, updated_by=by)
        await save_voice_input(db_conn, None, updated_by=by)
    lines = [r.getMessage() for r in caplog.records if r.name == "server.audit"]
    assert lines == [f"chat_options.voice_input by={by} old=False new=True",
                     f"chat_options.voice_input by={by} old=True new=None"]


# ---------- the admin route with voice input ----------

class _Engine:
    name, model, where = "local", "small", "this server"

    def __init__(self, fail=False):
        self.warm_calls, self.fail = 0, fail

    def warm(self):
        self.warm_calls += 1
        if self.fail:
            raise RuntimeError("no disk")


@pytest.fixture
def engine(monkeypatch):
    fake = _Engine()
    speech_service.reset_for_tests()
    monkeypatch.setattr(speech_service, "_transcriber", fake)
    yield fake
    speech_service.reset_for_tests()


async def test_put_voice_input_true_saves_and_warms_the_engine(db_conn, engine):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        r = await client.put("/v1/admin/chat-options", json={"voice_input": True})
    assert r.status_code == 200
    assert r.json() == {"web_search": "ask", "source": "default", "searxng": True,
                        "voice_input": True, "voice_source": "admin"}
    assert engine.warm_calls == 1


async def test_put_voice_input_false_does_not_warm(db_conn, engine):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        r = await client.put("/v1/admin/chat-options", json={"voice_input": False})
    assert r.json()["voice_input"] is False and engine.warm_calls == 0


async def test_a_reset_that_falls_back_to_env_on_warms(db_conn, engine, monkeypatch):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    monkeypatch.setenv("CHAT_VOICE_INPUT", "on")
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"voice_input": False})
        assert engine.warm_calls == 0
        r = await client.put("/v1/admin/chat-options", json={"voice_input": None})
    assert (r.json()["voice_input"], r.json()["voice_source"]) == (True, "env")
    assert engine.warm_calls == 1


async def test_a_warm_error_does_not_fail_the_save(db_conn, engine):
    engine.fail = True
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        r = await client.put("/v1/admin/chat-options", json={"voice_input": True})
    assert r.status_code == 200 and r.json()["voice_input"] is True
    assert engine.warm_calls == 1


async def test_put_with_neither_field_is_422(db_conn, engine):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        assert (await client.put("/v1/admin/chat-options", json={})).status_code == 422


async def test_put_web_search_null_resets_web_search_only(db_conn, engine):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"web_search": "off", "voice_input": True})
        r = await client.put("/v1/admin/chat-options", json={"web_search": None})
    assert (r.json()["web_search"], r.json()["source"]) == ("ask", "default")
    assert (r.json()["voice_input"], r.json()["voice_source"]) == (True, "admin")


async def test_one_body_can_set_both(db_conn, engine):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        r = await client.put("/v1/admin/chat-options", json={"web_search": "auto", "voice_input": True})
    assert (r.json()["web_search"], r.json()["voice_input"]) == ("auto", True)
    assert await _row(db_conn) == {"web_search": "auto", "voice_input": True}


async def test_a_web_search_save_does_not_re_warm_voice(db_conn, engine):
    """Saving web search must not retry a failed model download: only touching voice does."""
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    await save_voice_input(db_conn, True, updated_by=admin.user_id)
    async with _client(_app(db_conn, admin)) as client:
        await client.put("/v1/admin/chat-options", json={"web_search": "auto"})
    assert engine.warm_calls == 0


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
