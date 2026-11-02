"""Choosing and adding speech models from the admin console, and a choice reaching the speech routes."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router
from server.services import chat_options, console_overview
from server.services.chat_options import load_chat_options
from server.speech import catalogue, convert, service
from server.speech.convert import RepoFacts

URL = "/v1/admin/speech-models"
SHA = "c" * 40


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in ("SPEECH_ENGINE", "SPEECH_MODEL", "SPEECH_MODELS_FILE", "CHAT_VOICE_INPUT", "CHAT_WEB_SEARCH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(catalogue, "_installed", lambda name: True)
    catalogue.reset_for_tests()
    service.reset_for_tests()
    yield
    catalogue.reset_for_tests()
    service.reset_for_tests()


@pytest.fixture
async def admin(db_conn):
    return await resolve_or_provision_user(db_conn, iss="i", sub="admin", email="a@x.io")


@pytest.fixture
async def client(db_conn, admin):
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'chat_options'")
    user = admin
    app = FastAPI()

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=str(user["id"]), email=user["email"], role="admin")
    app.include_router(admin_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture
def hub(monkeypatch):
    """A Hub that knows one Transformers-format repo."""
    calls = []

    def inspect(repo, revision):
        calls.append((repo, revision))
        return RepoFacts(SHA, "converted-whisper", ("si",))

    monkeypatch.setattr(convert, "inspect_repo", inspect)
    return calls


async def test_the_list_shows_every_model_and_the_one_in_use(client):
    view = (await client.get(URL)).json()
    assert view["engine"] == "local" and view["chosen"] == "small" and view["source"] == "default"
    ids = [m["id"] for m in view["models"]]
    assert "small" in ids and "whisper-small-si-run15" in ids and "whisper-small-ta" in ids


async def test_a_server_setting_outside_the_list_is_shown_first(client, monkeypatch):
    monkeypatch.setenv("SPEECH_MODEL", "/models/mine")
    view = (await client.get(URL)).json()
    assert view["chosen"] == "/models/mine" and view["models"][0]["id"] == "/models/mine"
    assert view["source"] == "env"


async def test_a_remote_engine_has_no_list(client, monkeypatch):
    monkeypatch.setenv("SPEECH_ENGINE", "openai")
    assert (await client.get(URL)).json() == {"engine": "openai", "chosen": None, "source": "env", "models": []}


async def test_choose_saves_and_reset_goes_back(client, db_conn):
    res = await client.put(f"{URL}/choice", json={"id": "whisper-small-si-run15"})
    assert res.status_code == 200
    assert (res.json()["chosen"], res.json()["source"]) == ("whisper-small-si-run15", "admin")
    assert (await load_chat_options(db_conn)).speech_model == "whisper-small-si-run15"
    res = await client.put(f"{URL}/choice", json={"id": None})
    assert (res.json()["chosen"], res.json()["source"]) == ("small", "default")


async def test_choosing_leaves_the_other_settings_alone(client, db_conn, admin):
    await chat_options.save_voice_input(db_conn, False, updated_by=str(admin["id"]))
    await chat_options.save_chat_options(db_conn, "off", updated_by=str(admin["id"]))
    await client.put(f"{URL}/choice", json={"id": "tiny"})
    got = await load_chat_options(db_conn)
    assert (got.web_search, got.voice_input, got.speech_model) == ("off", False, "tiny")


async def test_choose_refuses_an_unknown_id_and_one_that_cant_run(client, monkeypatch):
    res = await client.put(f"{URL}/choice", json={"id": "nope"})
    assert res.status_code == 422 and res.json()["detail"]["error"] == "unknown_speech_model"
    monkeypatch.setattr(catalogue, "_installed", lambda name: False)
    res = await client.put(f"{URL}/choice", json={"id": "whisper-small-si-run15"})
    assert res.status_code == 422 and "transformers" in res.json()["detail"]["message"]


async def test_choosing_with_voice_on_starts_the_new_model_loading(client, db_conn, admin, monkeypatch):
    await chat_options.save_voice_input(db_conn, True, updated_by=str(admin["id"]))
    warmed = []
    monkeypatch.setattr("server.speech.local_whisper.LocalWhisper.warm", lambda self: warmed.append(self.model))
    await client.put(f"{URL}/choice", json={"id": "tiny"})
    assert warmed == ["tiny"]


async def test_choosing_with_voice_off_loads_nothing(client, monkeypatch):
    monkeypatch.setattr("server.speech.local_whisper.LocalWhisper.warm", lambda self: pytest.fail("no load"))
    await client.put(f"{URL}/choice", json={"id": "tiny"})


async def test_add_looks_it_up_pins_it_and_lists_it(client, hub, db_conn):
    res = await client.post(URL, json={"repo": "owner/Whisper-Si", "label": "Mine"})
    assert res.status_code == 201
    entry = next(m for m in res.json()["models"] if m["repo"] == "owner/Whisper-Si")
    assert entry["id"] == "owner-whisper-si" and entry["revision"] == SHA
    assert (entry["label"], entry["engine"], entry["languages"]) == ("Mine", "converted-whisper", ["si"])
    assert entry["removable"] is True
    assert hub == [("owner/Whisper-Si", None)]
    assert "owner-whisper-si" in (await load_chat_options(db_conn)).added_speech_models


async def test_an_added_model_can_be_chosen_and_reaches_a_worker(client, hub, db_conn):
    await client.post(URL, json={"repo": "owner/name"})
    await client.put(f"{URL}/choice", json={"id": "owner-name"})
    options = await load_chat_options(db_conn)
    engine = service.get_transcriber(wanted=catalogue.wanted(options))
    assert engine.spec.repo == "owner/name" and engine.spec.revision == SHA and engine.languages == ("si",)


async def test_languages_typed_in_win_over_the_model_card(client, hub):
    view = (await client.post(URL, json={"repo": "owner/name", "languages": ["ta"]})).json()
    assert next(m for m in view["models"] if m["id"] == "owner-name")["languages"] == ["ta"]


async def test_unconverted_runs_through_transformers_with_its_own_id(client, hub):
    await client.post(URL, json={"repo": "owner/name"})
    view = (await client.post(URL, json={"repo": "owner/name", "unconverted": True})).json()
    assert next(m for m in view["models"] if m["id"] == "owner-name-unconverted")["engine"] == "transformers-whisper"


async def test_unconverted_is_refused_for_a_ctranslate2_repo(client, monkeypatch):
    monkeypatch.setattr(convert, "inspect_repo", lambda r, v: RepoFacts(SHA, "faster-whisper", ()))
    res = await client.post(URL, json={"repo": "owner/name", "unconverted": True})
    assert res.status_code == 422 and res.json()["detail"]["error"] == "already_converted"


@pytest.mark.parametrize("body, code", [
    ({"repo": "not a repo"}, "bad_repo"),
    ({"repo": "../../etc/passwd"}, "bad_repo"),
    ({"repo": "/models/local"}, "bad_repo"),
    ({"repo": "owner/name", "revision": "-x"}, "bad_revision"),
    ({"repo": "owner/name", "languages": ["klingon"]}, "bad_language"),
])
async def test_add_refuses_bad_input_before_asking_the_hub(client, monkeypatch, body, code):
    monkeypatch.setattr(convert, "inspect_repo", lambda r, v: pytest.fail("the Hub wasn't needed"))
    res = await client.post(URL, json=body)
    assert res.status_code == 422 and res.json()["detail"]["error"] == code


@pytest.mark.parametrize("error, status, code", [
    (convert.NotOnHub("x"), 422, "not_on_hub"),
    (convert.NotWhisper("x"), 422, "not_whisper"),
    (convert.HubUnreachable("x"), 503, "hub_unreachable"),
])
async def test_add_says_what_the_hub_said(client, monkeypatch, error, status, code):
    def inspect(repo, revision):
        raise error
    monkeypatch.setattr(convert, "inspect_repo", inspect)
    res = await client.post(URL, json={"repo": "owner/name"})
    assert res.status_code == status and res.json()["detail"]["error"] == code


async def test_adding_the_same_repo_twice_is_refused(client, hub):
    await client.post(URL, json={"repo": "owner/name"})
    res = await client.post(URL, json={"repo": "owner/name"})
    assert res.status_code == 409 and res.json()["detail"]["error"] == "already_listed"


async def test_add_refuses_a_model_this_server_cant_run(client, hub, monkeypatch):
    monkeypatch.setattr(catalogue, "_installed", lambda name: False)
    res = await client.post(URL, json={"repo": "owner/name"})
    assert res.status_code == 422 and res.json()["detail"]["error"] == "speech_model_cannot_run"


async def test_two_admins_adding_at_once_both_land(client, hub, db_conn, admin):
    by = str(admin["id"])
    await chat_options.add_speech_model(db_conn, "a", {"repo": "o/a", "engine": "faster-whisper"}, updated_by=by)
    await chat_options.add_speech_model(db_conn, "b", {"repo": "o/b", "engine": "faster-whisper"}, updated_by=by)
    await chat_options.save_voice_input(db_conn, True, updated_by=by)
    got = await load_chat_options(db_conn)
    assert set(got.added_speech_models) == {"a", "b"} and got.voice_input is True


async def test_remove_takes_it_off_and_a_chosen_one_falls_back(client, hub, db_conn):
    await client.post(URL, json={"repo": "owner/name"})
    await client.put(f"{URL}/choice", json={"id": "owner-name"})
    view = (await client.delete(f"{URL}/owner-name")).json()
    assert "owner-name" not in [m["id"] for m in view["models"]] and view["chosen"] == "small"
    cur = await db_conn.execute("SELECT count(*) FROM app_settings WHERE key = 'chat_options'")
    assert (await cur.fetchone())[0] == 0     # nothing left, so the row is gone


async def test_remove_keeps_the_other_settings(client, hub, db_conn, admin):
    await chat_options.save_voice_input(db_conn, False, updated_by=str(admin["id"]))
    await client.post(URL, json={"repo": "owner/name"})
    await client.delete(f"{URL}/owner-name")
    got = await load_chat_options(db_conn)
    assert got.added_speech_models == {} and (got.voice_input, got.voice_source) == (False, "admin")


async def test_a_built_in_model_cant_be_removed(client):
    res = await client.delete(f"{URL}/whisper-small-ta")
    assert res.status_code == 404


async def test_a_row_that_holds_junk_is_ignored(client, db_conn):
    await db_conn.execute("""INSERT INTO app_settings (key, value) VALUES ('chat_options',
        '{"speech_model": 7, "added_speech_models": "junk"}'::jsonb)""")
    view = (await client.get(URL)).json()
    assert view["chosen"] == "small" and view["source"] == "default"


async def test_overview_names_the_chosen_model(db_conn, admin):
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'chat_options'")
    await chat_options.save_voice_input(db_conn, True, updated_by=str(admin["id"]))
    await chat_options.save_speech_model(db_conn, "whisper-small-ta", updated_by=str(admin["id"]))
    options = await load_chat_options(db_conn)
    check = await console_overview._voice_input(options, {})
    assert check.state == "warn" and "whisper-small-ta" in check.summary
