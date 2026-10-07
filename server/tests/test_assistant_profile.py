"""v2.4 Task A2: the deployment's assistant profile (its "soul") — admin-set,
or from a file, or none; it leads the system message and the app's rules
follow it."""
from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router
from server.services.assistant_profile import (PROFILE_MAX_CHARS, Profile, clean_profile, load_profile,
                                               profile_warnings, save_profile)


@pytest.fixture(autouse=True)
def _no_profile_file(monkeypatch):
    monkeypatch.delenv("CHAT_ASSISTANT_PROFILE_FILE", raising=False)


async def test_the_profile_comes_from_the_admin_then_the_file_then_nothing(db_conn, tmp_path, monkeypatch):
    f = tmp_path / "assistant.md"
    f.write_text("You are Ada, the library's assistant.\n", encoding="utf-8")
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(f))
    assert await load_profile(db_conn) == Profile("You are Ada, the library's assistant.", "file")
    await save_profile(db_conn, "Be brief.", updated_by=None)
    assert await load_profile(db_conn) == Profile("Be brief.", "admin")
    await save_profile(db_conn, "", updated_by=None)                 # reset: the file applies again
    assert (await load_profile(db_conn)).source == "file"
    monkeypatch.delenv("CHAT_ASSISTANT_PROFILE_FILE")
    assert await load_profile(db_conn) == Profile("", "none")


async def test_an_edited_file_is_read_again(db_conn, tmp_path, monkeypatch):
    f = tmp_path / "assistant.md"
    f.write_text("First.", encoding="utf-8")
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(f))
    assert (await load_profile(db_conn)).text == "First."
    f.write_text("Second.", encoding="utf-8")
    assert (await load_profile(db_conn)).text == "Second."


async def test_a_missing_file_is_no_profile_and_says_so_without_its_contents(db_conn, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(tmp_path / "nope.md"))
    with caplog.at_level(logging.WARNING):
        assert await load_profile(db_conn) == Profile("", "none")
    assert "CHAT_ASSISTANT_PROFILE_FILE" in caplog.text


def test_profile_text_is_cleaned_capped_and_tool_names_warned():
    assert clean_profile("Hi\x00 there\x1b[31m\n\tok  ") == "Hi there[31m\n\tok"
    assert len(clean_profile("x" * 9000)) == PROFILE_MAX_CHARS
    assert profile_warnings("Always use web_search.") and not profile_warnings("Be kind.")


# ---------- the admin routes ----------

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
    return deps.Principal(user_id=u["id"], email=u["email"], role=role)


async def test_the_console_sees_the_file_default(db_conn, tmp_path, monkeypatch):
    f = tmp_path / "assistant.md"
    f.write_text("From the file.", encoding="utf-8")
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(f))
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        body = (await client.get("/v1/admin/assistant")).json()
    assert body["source"] == "file" and body["text"] == "From the file." and body["fileConfigured"] is True


async def test_only_admins_edit_the_profile_and_its_text_is_never_logged(db_conn, caplog):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")       # claims the seed admin
    member = await _principal(db_conn, "s2", "b@x.io", "member")
    async with _client(_app(db_conn, member)) as client:
        assert (await client.put("/v1/admin/assistant", json={"text": "x"})).status_code == 403
        assert (await client.get("/v1/admin/assistant")).status_code == 403
    with caplog.at_level(logging.DEBUG):
        async with _client(_app(db_conn, admin)) as client:
            r = await client.put("/v1/admin/assistant", json={"text": "You are Ada, SECRET-TONE."})
            assert r.status_code == 200
            body = r.json()
            assert body["source"] == "admin" and body["text"] == "You are Ada, SECRET-TONE."
            assert body["preview"].startswith("You are Ada, SECRET-TONE.\n\nYou are working in Natural Reader")
            assert body["maxChars"] == PROFILE_MAX_CHARS and body["warnings"] == []
            assert (await client.put("/v1/admin/assistant", json={"text": "x" * 8001})).status_code == 422
            r = await client.put("/v1/admin/assistant", json={"text": "Use web_search a lot."})
            assert r.json()["warnings"]
            r = await client.put("/v1/admin/assistant", json={"text": ""})
            assert r.json()["source"] == "none" and r.json()["fileConfigured"] is False
            assert (await client.get("/v1/admin/assistant")).json()["source"] == "none"
    assert "SECRET-TONE" not in caplog.text
