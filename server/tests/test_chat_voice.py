"""Who speaks the chat and Talk (spec 2026-11-03-google-speech): Kokoro, or Google Gemini-TTS. No request reaches
Google: the service takes an httpx transport and a token function."""
from __future__ import annotations

import base64
import io
import json
import logging
from contextlib import asynccontextmanager

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from httpx import ASGITransport

from server import chat_voice, endpoints, google_cloud
from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.chat_voice import ChatVoice, GoogleVoiceError, current, google_speak, language_of
from server.routers import admin as admin_router
from server.routers import chat_voice as voice_router
from server.routers import speech as speech_router
from server.services import chat_options
from server.services.chat_options import ChatOptions, load_chat_options

TOKEN = "ya29.SECRET-token"
MODEL = "gemini-2.5-flash-tts"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in ("CHAT_VOICE", "GOOGLE_TTS_MODEL", "GOOGLE_TTS_VOICE", "GOOGLE_TTS_LANGUAGE", "GOOGLE_TIMEOUT_S"):
        monkeypatch.delenv(key, raising=False)


def _wav(seconds=0.5, rate=24000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * rate), dtype=np.float32), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def _token():
    return TOKEN, "proj-1"


def _speak(handler, text="Hello there.", voice="Kore", speed=1.0, env=None):
    return google_speak(text, voice, speed, env or {"GOOGLE_TTS_MODEL": MODEL},
                        transport=httpx.MockTransport(handler), token=_token)


def _audio(_request=None):
    return httpx.Response(200, json={"audioContent": base64.b64encode(_wav()).decode()})


def _options(chat_voice_value=None):
    return ChatOptions("ask", "default", True, "admin", chat_voice=chat_voice_value)


# ---------- the language of a sentence ----------

@pytest.mark.parametrize("text, code", [
    ("ශ්‍රී ලංකාව ලස්සන රටකි.", "si-LK"),
    ("இலங்கை ஒரு அழகான நாடு.", "ta-IN"),
    ("Sri Lanka is a beautiful country.", "en-US"),
    ("මම Python ඉගෙන ගන්නවා.", "si-LK"),   # mostly Sinhala, an English word inside
    ("42 — 7.", "en-US"),
])
def test_the_language_comes_from_the_script(text, code):
    assert language_of(text, {}) == code


def test_other_text_takes_the_configured_language():
    assert language_of("Colour.", {"GOOGLE_TTS_LANGUAGE": "en-GB"}) == "en-GB"


# ---------- which voice applies ----------

def test_kokoro_unless_someone_chooses_google():
    assert current(_options(), {}) == ChatVoice("kokoro", None, "default")


def test_google_from_the_environment_needs_its_model():
    assert current(_options(), {"CHAT_VOICE": "google"}).engine == "kokoro"
    got = current(_options(), {"CHAT_VOICE": "google", "GOOGLE_TTS_MODEL": MODEL})
    assert got == ChatVoice("google", "Kore", "env")
    got = current(_options(), {"CHAT_VOICE": "google", "GOOGLE_TTS_MODEL": MODEL, "GOOGLE_TTS_VOICE": "Puck"})
    assert got.voice == "Puck"
    got = current(_options(), {"CHAT_VOICE": "google", "GOOGLE_TTS_MODEL": MODEL, "GOOGLE_TTS_VOICE": "Nobody"})
    assert got.voice == "Kore"


def test_the_admins_choice_wins_while_it_can_run():
    env = {"GOOGLE_TTS_MODEL": MODEL}
    assert current(_options({"engine": "google", "voice": "Charon"}), env) == ChatVoice("google", "Charon", "admin")
    assert current(_options({"engine": "google", "voice": "Charon"}), {}).engine == "kokoro"
    assert current(_options({"engine": "kokoro"}), {**env, "CHAT_VOICE": "google"}) == ChatVoice(
        "kokoro", None, "admin")


# ---------- speaking through Google ----------

async def test_a_sentence_goes_to_gemini_tts_with_the_model_voice_and_language():
    seen = []

    def handler(request):
        seen.append(request)
        return _audio()
    wav, seconds = await _speak(handler, text="ශ්‍රී ලංකාව.")
    request = seen[0]
    assert str(request.url) == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-goog-user-project"] == "proj-1"
    body = json.loads(request.content)
    assert body["input"] == {"text": "ශ්‍රී ලංකාව."}
    assert body["voice"] == {"languageCode": "si-LK", "name": "Kore", "modelName": MODEL}
    assert body["audioConfig"] == {"audioEncoding": "LINEAR16"}
    assert sf.read(io.BytesIO(wav))[1] == 24000 and abs(seconds - 0.5) < 0.01


async def test_a_speed_other_than_one_is_sent_as_the_speaking_rate():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _audio()
    await _speak(handler, speed=1.25)
    assert bodies[0]["audioConfig"] == {"audioEncoding": "LINEAR16", "speakingRate": 1.25}


@pytest.mark.parametrize("code, status, person, admin", [
    (400, 422, "nope", "nope"),   # Google's own words for a bad request
    (401, 503, "Ask your admin", "refused this server's key"),
    (403, 503, "Ask your admin", "Vertex AI User"),
    (429, 503, "quota", "quota"),
    (500, 502, "didn't answer", "didn't answer"),
])
async def test_each_refusal_tells_the_person_and_the_admin_what_they_need(code, status, person, admin, caplog):
    caplog.set_level(logging.WARNING)
    with pytest.raises(GoogleVoiceError) as caught:
        await _speak(lambda r: httpx.Response(code, json={"error": {"message": "nope"}}))
    assert caught.value.status == status and person in str(caught.value)
    assert admin in caught.value.detail and admin in caplog.text   # the admin finds it in the log


async def test_a_hung_google_is_a_gateway_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(GoogleVoiceError) as caught:
        await _speak(handler)
    assert caught.value.status == 504 and "didn't answer" in str(caught.value)


async def test_credentials_that_dont_work_say_so():
    async def broken():
        raise google_cloud.GoogleAuthError("The Google key file isn't a service-account key.")
    with pytest.raises(GoogleVoiceError) as caught:
        await google_speak("Hi.", "Kore", 1.0, {"GOOGLE_TTS_MODEL": MODEL}, transport=httpx.MockTransport(_audio),
                           token=broken)
    assert caught.value.status == 503 and "Ask your admin" in str(caught.value)
    assert "service-account key" in caught.value.detail


async def test_the_token_never_reaches_a_log_or_an_error(caplog):
    caplog.set_level(logging.DEBUG)
    for code in (400, 401, 403, 429, 500):
        with pytest.raises(GoogleVoiceError) as caught:
            await _speak(lambda r, c=code: httpx.Response(c, json={"error": {"message": "m"}}))
        assert TOKEN not in str(caught.value)
    assert TOKEN not in caplog.text


# ---------- the route ----------

class Pool:
    def __init__(self, db_conn):
        self.db_conn = db_conn

    @asynccontextmanager
    async def __call__(self):
        yield self.db_conn


def _principal(caps=frozenset({"chat", "reader"})):
    return deps.Principal(user_id="u1", email="e@x.io", role="member", capabilities=frozenset(caps))


@pytest.fixture
def who(monkeypatch):
    holder = {"p": _principal()}

    async def resolve(request, conn):
        return holder["p"]
    monkeypatch.setattr(deps, "resolve_principal", resolve)
    return holder


@pytest.fixture
async def voice_client(db_conn, who):
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'chat_options'")
    app = FastAPI()
    app.dependency_overrides[speech_router.speech_conn] = lambda: Pool(db_conn)
    app.include_router(voice_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _choose(db_conn, value):
    user = await resolve_or_provision_user(db_conn, iss="i", sub="chooser", email="c@x.io")
    await chat_options.save_chat_voice(db_conn, value, updated_by=str(user["id"]))


async def test_with_kokoro_the_route_reads_with_the_persons_own_voice(voice_client, monkeypatch):
    spoken = []

    def speak(text, voice, speed):
        spoken.append((text, voice, speed))
        return np.zeros(2400, dtype=np.float32), 24000
    monkeypatch.setattr(endpoints, "_speak", speak)
    r = await voice_client.post("/v1/chat/voice", json={"text": "Hello.", "voice": "bf_emma", "speed": 1.1})
    assert r.status_code == 200 and r.json()["audio_base64"]
    assert spoken == [("Hello.", "bf_emma", 1.1)]


async def test_with_google_the_route_reads_with_the_admins_voice(voice_client, db_conn, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_MODEL", MODEL)
    await _choose(db_conn, {"engine": "google", "voice": "Charon"})
    calls = []

    async def speak(text, voice, speed, env, **_):
        calls.append((text, voice, speed))
        return _wav(), 0.5
    monkeypatch.setattr(chat_voice, "google_speak", speak)
    monkeypatch.setattr(endpoints, "_speak", lambda *a: pytest.fail("Kokoro must not speak"))
    r = await voice_client.post("/v1/chat/voice", json={"text": "Hello.", "voice": "bf_emma", "speed": 1.1})
    assert r.status_code == 200 and r.json()["duration_seconds"] == 0.5
    assert base64.b64decode(r.json()["audio_base64"]) == _wav()
    assert calls == [("Hello.", "Charon", 1.1)]


async def test_googles_refusal_reaches_the_person_in_words(voice_client, db_conn, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_MODEL", MODEL)
    await _choose(db_conn, {"engine": "google", "voice": "Kore"})

    async def speak(*a, **k):
        raise GoogleVoiceError(503, "Google's quota for voices is used up for now")
    monkeypatch.setattr(chat_voice, "google_speak", speak)
    r = await voice_client.post("/v1/chat/voice", json={"text": "Hello."})
    assert r.status_code == 503 and r.json()["detail"]["message"] == "Google's quota for voices is used up for now"


async def test_a_sentence_over_googles_limit_is_refused_before_sending(voice_client, db_conn, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_MODEL", MODEL)
    await _choose(db_conn, {"engine": "google", "voice": "Kore"})
    monkeypatch.setattr(chat_voice, "google_speak", lambda *a, **k: pytest.fail("nothing sent"))
    r = await voice_client.post("/v1/chat/voice", json={"text": "ශ" * 1400})   # 3 bytes a letter: 4,200 bytes
    assert r.status_code == 413


@pytest.mark.parametrize("caps", [{"reader"}, {"chat"}])
async def test_it_needs_chat_and_read_aloud(voice_client, who, caps):
    who["p"] = _principal(caps)
    assert (await voice_client.post("/v1/chat/voice", json={"text": "Hi."})).status_code == 403


async def test_anyone_who_chats_can_ask_who_speaks(voice_client, db_conn, monkeypatch, who):
    who["p"] = _principal({"chat"})
    assert (await voice_client.get("/v1/chat/voice")).json() == {"engine": "kokoro", "voice": None,
                                                                 "where": "this server"}
    monkeypatch.setenv("GOOGLE_TTS_MODEL", MODEL)
    await _choose(db_conn, {"engine": "google", "voice": "Puck"})
    assert (await voice_client.get("/v1/chat/voice")).json() == {"engine": "google", "voice": "Puck",
                                                                 "where": "Google Cloud"}


# ---------- the admin's switch ----------

@pytest.fixture
async def admin_client(db_conn):
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'chat_options'")
    user = await resolve_or_provision_user(db_conn, iss="i", sub="admin", email="a@x.io")
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=str(user["id"]), email=user["email"], role="admin")
    app.include_router(admin_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_the_admin_sees_the_engines_and_googles_voices(admin_client):
    view = (await admin_client.get("/v1/admin/chat-voice")).json()
    assert (view["engine"], view["source"]) == ("kokoro", "default")
    engines = {e["id"]: e for e in view["engines"]}
    assert engines["kokoro"]["available"] is True
    assert engines["google"]["available"] is False and engines["google"]["needs"] == "GOOGLE_TTS_MODEL"
    assert len(view["voices"]) == 30 and "Kore" in view["voices"]


async def test_google_cant_be_chosen_until_it_is_set_up(admin_client, db_conn):
    r = await admin_client.put("/v1/admin/chat-voice", json={"engine": "google"})
    assert r.status_code == 422 and "GOOGLE_TTS_MODEL" in r.json()["detail"]["message"]
    assert (await load_chat_options(db_conn)).chat_voice is None


async def test_choosing_google_and_a_voice_then_going_back(admin_client, db_conn, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_MODEL", MODEL)
    view = (await admin_client.put("/v1/admin/chat-voice", json={"engine": "google", "voice": "Puck"})).json()
    assert (view["engine"], view["voice"], view["source"], view["model"]) == ("google", "Puck", "admin", MODEL)
    assert (await load_chat_options(db_conn)).chat_voice == {"engine": "google", "voice": "Puck"}
    r = await admin_client.put("/v1/admin/chat-voice", json={"engine": "google", "voice": "Nobody"})
    assert r.status_code == 422
    view = (await admin_client.put("/v1/admin/chat-voice", json={"engine": None})).json()
    assert (view["engine"], view["source"]) == ("kokoro", "default")


async def test_the_voice_switch_leaves_the_other_settings_alone(admin_client, db_conn, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_MODEL", MODEL)
    user = await resolve_or_provision_user(db_conn, iss="i", sub="admin", email="a@x.io")
    await chat_options.save_voice_input(db_conn, True, updated_by=str(user["id"]))
    await admin_client.put("/v1/admin/chat-voice", json={"engine": "google"})
    got = await load_chat_options(db_conn)
    assert (got.voice_input, got.chat_voice) == (True, {"engine": "google", "voice": "Kore"})
