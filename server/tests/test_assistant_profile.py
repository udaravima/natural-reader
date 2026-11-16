"""The assistant's profiles as an admin keeps them (v2.4 Task A2; the profile library spec, 2026-11-16): the file
while the library is empty, and the admin routes for house rules and named profiles."""
from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router
from server.chat import profile_text
from server.services import profile_library as lib
from server.services.assistant_profile import PROFILE_MAX_CHARS, clean_profile, profile_warnings


@pytest.fixture(autouse=True)
def _no_profile_file(monkeypatch):
    monkeypatch.delenv("CHAT_ASSISTANT_PROFILE_FILE", raising=False)




async def _text(conn):
    return (await profile_text.resolve(conn, session_id=None, asked_id=None)).text


async def test_an_edited_file_is_read_again(db_conn, tmp_path, monkeypatch):
    f = tmp_path / "assistant.md"
    f.write_text("First.", encoding="utf-8")
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(f))
    assert await _text(db_conn) == "First."
    f.write_text("Second.", encoding="utf-8")
    assert await _text(db_conn) == "Second."


async def test_a_missing_file_is_no_profile_and_says_so_without_its_contents(db_conn, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(tmp_path / "nope.md"))
    with caplog.at_level(logging.WARNING):
        assert await _text(db_conn) == ""
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


async def test_the_console_sees_the_file_while_the_library_is_empty(db_conn, tmp_path, monkeypatch):
    f = tmp_path / "assistant.md"
    f.write_text("From the file.", encoding="utf-8")
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(f))
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        body = (await client.get("/v1/admin/assistant/profiles")).json()
        assert body["profiles"] == [] and body["file"] == {"configured": True, "inUse": True, "text": "From the file."}
        await client.post("/v1/admin/assistant/profiles", json={"name": "Plain", "text": "You are plain."})
        body = (await client.get("/v1/admin/assistant/profiles")).json()
    assert body["file"] == {"configured": True, "inUse": False, "text": ""}


async def test_only_admins_reach_the_library_and_its_text_is_never_logged(db_conn, caplog):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")       # claims the seed admin
    member = await _principal(db_conn, "s2", "b@x.io", "member")
    async with _client(_app(db_conn, member)) as client:
        for method, path in (("get", "/profiles"), ("post", "/profiles"), ("put", "/profiles/x"), ("delete", "/profiles/x"),
                             ("put", "/profiles/order"), ("put", "/house-rules"), ("post", "/preview")):
            r = await client.request(method, "/v1/admin/assistant" + path, json={} if method != "get" else None)
            assert r.status_code == 403, path
    with caplog.at_level(logging.DEBUG):
        async with _client(_app(db_conn, admin)) as client:
            r = await client.put("/v1/admin/assistant/house-rules", json={"text": "HOUSE-SECRET: be kind."})
            assert r.status_code == 200 and r.json()["houseRules"]["text"] == "HOUSE-SECRET: be kind."
            r = await client.post("/v1/admin/assistant/profiles",
                                  json={"name": "Ada", "description": "Friendly", "text": "You are Ada, SECRET-TONE."})
            assert r.status_code == 200
            view = r.json()
            (ada,) = view["profiles"]
            assert (ada["name"], ada["description"], ada["text"], ada["enabled"], ada["isDefault"], ada["chats"]) == (
                "Ada", "Friendly", "You are Ada, SECRET-TONE.", True, True, 0)
            assert ada["warnings"] == [] and ada["combinedChars"] == len("HOUSE-SECRET: be kind.\n\nYou are Ada, SECRET-TONE.")
            assert view["maxChars"] == PROFILE_MAX_CHARS and view["houseRules"]["maxChars"] == 2000
            assert view["placeholders"] == ["date", "app", "profile"]
    assert "SECRET-TONE" not in caplog.text and "HOUSE-SECRET" not in caplog.text and "Friendly" not in caplog.text


async def test_the_library_routes(db_conn):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as c:
        base = "/v1/admin/assistant/profiles"
        a = (await c.post(base, json={"name": "Plain", "text": "You are plain."})).json()["profiles"][0]
        view = (await c.post(base, json={"name": "Tutor", "text": "Use web_search. Today is {{dat}}."})).json()
        b = view["profiles"][1]
        assert len(b["warnings"]) == 2 and "web_search" in b["warnings"][0] and "{{dat}}" in b["warnings"][1]

        async def err(r):
            return r.status_code, r.json()["detail"]["error"]
        assert await err(await c.post(base, json={"name": "plain", "text": "x"})) == (409, "name_taken")
        assert (await c.post(base, json={"name": "", "text": "x"})).status_code == 422
        assert (await c.post(base, json={"name": "X", "text": "x" * 8001})).status_code == 422
        assert await err(await c.post(base, json={"name": "X", "text": "   "})) == (422, "invalid")
        assert await err(await c.put(f"{base}/{a['id']}", json={"enabled": False})) == (409, "default_required")
        assert await err(await c.delete(f"{base}/{a['id']}")) == (409, "default_required")
        assert await err(await c.put(f"{base}/00000000-0000-0000-0000-000000000000", json={"name": "Z"})) == (404, "not_found")
        assert await err(await c.delete(f"{base}/junk")) == (404, "not_found")

        view = (await c.put(f"{base}/{b['id']}", json={"isDefault": True, "name": "Teacher"})).json()
        assert [(p["name"], p["isDefault"]) for p in view["profiles"]] == [("Plain", False), ("Teacher", True)]
        view = (await c.put(f"{base}/order", json={"ids": [b["id"], a["id"]]})).json()
        assert [p["name"] for p in view["profiles"]] == ["Teacher", "Plain"]
        r = await c.delete(f"{base}/{a['id']}")
        assert r.status_code == 200 and r.json()["chats"] == 0 and [p["name"] for p in r.json()["profiles"]] == ["Teacher"]
        assert (await c.put("/v1/admin/assistant/house-rules", json={"text": "x" * 2001})).status_code == 422


async def test_the_preview_shows_the_text_being_edited_filled_in(db_conn):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    await lib.save_house_rules(db_conn, "Be kind.", updated_by=None)
    async with _client(_app(db_conn, admin)) as c:
        r = await c.post("/v1/admin/assistant/preview", json={"name": "Ada", "text": "You are {{profile}} in {{app}}. {{nope}}"})
        body = r.json()
        assert body["preview"].startswith("Be kind.\n\nYou are Ada in Natural Reader. {{nope}}\n\nToday is ")
        assert "You are working in" not in body["preview"]            # {{app}} placed it
        assert body["combinedChars"] == len("Be kind.\n\nYou are {{profile}} in {{app}}. {{nope}}")
        assert len(body["warnings"]) == 1 and "{{nope}}" in body["warnings"][0]
        r = await c.post("/v1/admin/assistant/preview", json={"name": "", "text": ""})    # house rules alone
        assert r.json()["preview"].startswith("Be kind.\n\nYou are the assistant in Natural Reader")
    assert await lib.list_profiles(db_conn) == []                      # a preview saves nothing


async def test_people_see_names_and_descriptions_never_text(db_conn):
    from server.routers import chat_profiles
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    member = deps.Principal(user_id=u["id"], email=u["email"], role="member", capabilities=frozenset({"chat"}))
    a = await lib.create_profile(db_conn, name="Plain", description="Short answers", text="TEXT-A", updated_by=None)
    b = await lib.create_profile(db_conn, name="Tutor", description="", text="TEXT-B", updated_by=None)
    off = await lib.create_profile(db_conn, name="Off", description="", text="TEXT-C", updated_by=None)
    await lib.update_profile(db_conn, off.id, enabled=False, updated_by=None)
    app = _app(db_conn, member)
    app.include_router(chat_profiles.router)
    async with _client(app) as c:
        r = await c.get("/v1/chat/profiles")
    assert r.json() == {"profiles": [{"id": a.id, "name": "Plain", "description": "Short answers", "isDefault": True},
                                     {"id": b.id, "name": "Tutor", "description": "", "isDefault": False}]}
    assert "TEXT" not in r.text
