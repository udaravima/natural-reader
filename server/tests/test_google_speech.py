"""Google Cloud speech (spec 2026-11-03-google-speech): the credentials and the `google` engine. No request reaches
Google: the engine takes an httpx transport and a token function, as the OpenAI engine does."""
from __future__ import annotations

import base64
import io
import json
import logging

import httpx
import numpy as np
import pytest
import soundfile as sf

from server import google_cloud
from server.speech import google_engine
from server.speech.base import AudioTooLong, EngineFailed, EngineUnavailable
from server.speech.google_engine import GoogleSpeech

NAME = "projects/p-1/locations/global/recognizers/r-1"
TOKEN = "ya29.SECRET-token"


def _wav(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, (0.1 * np.sin(np.arange(int(seconds * rate)) / 3)).astype(np.float32), rate, format="WAV")
    return buf.getvalue()


async def _token():
    return TOKEN, "p-1"


def _engine(handler, name=NAME, **kw):
    return GoogleSpeech(name, transport=httpx.MockTransport(handler), token=_token, **kw)


RECOGNIZER = {"name": NAME, "model": "long", "languageCodes": ["en-GB", "si-LK", "ta-IN"]}


def _answer(results):
    return httpx.Response(200, json={"results": results})


# ---------- configuration ----------

@pytest.mark.parametrize("name, host", [
    (NAME, "speech.googleapis.com"),
    ("projects/p/locations/asia-southeast1/recognizers/r", "asia-southeast1-speech.googleapis.com"),
    ("projects/p/locations/eu/recognizers/r", "eu-speech.googleapis.com"),
])
def test_the_location_in_the_name_picks_the_endpoint(name, host):
    engine = GoogleSpeech(name)
    assert engine.endpoint == f"https://{host}"
    assert engine.where == "Google Cloud"


@pytest.mark.parametrize("name", [None, "", "asr-si-ta", "projects/p/recognizers/r", "projects/p/locations/l/recognizers/"])
async def test_a_recognizer_that_isnt_a_full_name_is_a_status_not_a_crash(name):
    engine = GoogleSpeech(name)
    status = await engine.status()
    assert (status.ready, status.state) == (False, "error")
    assert "projects/" in status.detail and "GOOGLE_SPEECH_RECOGNIZER" in status.detail
    with pytest.raises(EngineUnavailable):
        await engine.transcribe(_wav(1), "audio/wav", None)


async def test_status_reads_the_recognizer_once_for_its_model_and_languages():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=RECOGNIZER)
    engine = _engine(handler)
    status = await engine.status()
    await engine.status()
    assert status.ready and status.state == "ready"
    assert len(seen) == 1
    assert str(seen[0].url) == f"https://speech.googleapis.com/v2/{NAME}"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert (engine.model, engine.languages) == ("long", ("en", "si", "ta"))


@pytest.mark.parametrize("code, words", [(403, "can't use this recognizer"), (404, "No such recognizer"),
                                         (401, "refused this server's key")])
async def test_a_recognizer_it_cant_use_says_why(code, words):
    status = await _engine(lambda r: httpx.Response(code, json={"error": {"message": "x"}})).status()
    assert status.state == "error" and words in status.detail


# What Google answered live (2026-11-03) for a recognizer name whose project doesn't have Speech-to-Text on.
SERVICE_OFF = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                         "message": "Cloud Speech-to-Text API has not been used in project p-9 before or it is disabled.",
                         "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED",
                                      "metadata": {"serviceTitle": "Cloud Speech-to-Text API",
                                                   "service": "speech.googleapis.com",
                                                   "consumer": "projects/p-9"}}]}}


async def test_a_403_for_an_api_thats_off_names_the_api_and_the_project_not_a_role():
    engine = _engine(lambda r: httpx.Response(403, json=SERVICE_OFF))
    status = await engine.status()
    assert "Cloud Speech-to-Text API is off in project p-9" in status.detail
    assert "GOOGLE_SPEECH_RECOGNIZER" in status.detail and "Cloud Speech Client" not in status.detail
    with pytest.raises(EngineUnavailable, match="off in project p-9"):
        await engine.transcribe(_wav(1), "audio/wav", None)


@pytest.mark.parametrize("codes, hears", [
    (["si-LK", "ta-IN", "en-GB"], "si"),   # live 2026-11-03, model long: English came back as Sinhala
    (["en-GB"], None),                     # one language: there is nothing else Auto could mean
    (["auto"], None),                      # chirp_3's own detection
    ([], None),
])
async def test_auto_on_a_recognizer_with_several_languages_hears_the_first(codes, hears):
    engine = _engine(lambda r: httpx.Response(200, json={**RECOGNIZER, "languageCodes": codes}))
    assert engine.auto_hears is None   # unknown until the recognizer is read
    await engine.status()
    assert engine.auto_hears == hears


async def test_credentials_that_dont_work_are_a_status_in_words():
    async def broken():
        raise google_cloud.GoogleAuthError("The key file isn't a service-account key.")
    engine = GoogleSpeech(NAME, transport=httpx.MockTransport(lambda r: httpx.Response(200)), token=broken)
    status = await engine.status()
    assert status.state == "error" and "service-account key" in status.detail


# ---------- recognizing ----------

async def test_a_clip_goes_as_16_khz_wav_with_nothing_else_overridden():
    seen = []

    def handler(request):
        seen.append(request)
        return _answer([{"alternatives": [{"transcript": "hello there"}], "languageCode": "en-gb"}])
    result = await _engine(handler).transcribe(_wav(2, rate=48000), "audio/wav", None)
    request = seen[0]
    assert str(request.url) == f"https://speech.googleapis.com/v2/{NAME}:recognize"
    body = json.loads(request.content)
    assert body["config"] == {"autoDecodingConfig": {}}
    assert body["configMask"] == "autoDecodingConfig"
    sent, rate = sf.read(io.BytesIO(base64.b64decode(body["content"])))
    assert rate == 16000 and sent.ndim == 1 and abs(len(sent) / rate - 2) < 0.05
    assert (result.text, result.language, result.no_speech, result.avg_logprob) == ("hello there", "en", None, None)
    assert abs(result.duration_s - 2) < 0.05


async def test_a_named_language_overrides_the_recognizers_with_its_own_code():
    bodies = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=RECOGNIZER)
        bodies.append(json.loads(request.content))
        return _answer([])
    engine = _engine(handler)
    await engine.status()
    await engine.transcribe(_wav(1), "audio/wav", "en")
    assert bodies[0]["config"]["languageCodes"] == ["en-GB"]   # the recognizer's English, not en-US
    assert bodies[0]["configMask"] == "autoDecodingConfig,languageCodes"


@pytest.mark.parametrize("code, bcp", [("si", "si-LK"), ("ta", "ta-IN"), ("en", "en-US"), ("fr", "fr")])
async def test_without_the_recognizers_list_a_language_takes_its_usual_code(code, bcp):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _answer([])
    await _engine(handler).transcribe(_wav(1), "audio/wav", code)
    assert bodies[0]["config"]["languageCodes"] == [bcp]


async def test_every_result_is_joined_and_no_results_is_no_text():
    many = [{"alternatives": [{"transcript": " One. "}]}, {"alternatives": [{"transcript": "Two."}]},
            {"alternatives": []}]
    assert (await _engine(lambda r: _answer(many)).transcribe(_wav(1), "audio/wav", None)).text == "One. Two."
    nothing = await _engine(lambda r: httpx.Response(200, json={})).transcribe(_wav(1), "audio/wav", None)
    assert (nothing.text, nothing.language) == ("", None)


async def test_a_clip_over_googles_minute_is_refused_before_sending():
    sent = []
    engine = _engine(lambda r: sent.append(r) or _answer([]))
    with pytest.raises(AudioTooLong):
        await engine.transcribe(_wav(61), "audio/wav", None)
    assert sent == []


@pytest.mark.parametrize("code, kind, words", [
    (401, EngineUnavailable, "refused this server's key"),
    (403, EngineUnavailable, "can't use this recognizer"),
    (404, EngineUnavailable, "No such recognizer"),
    (429, EngineFailed, "quota"),
    (500, EngineFailed, "didn't answer"),
])
async def test_each_refusal_is_its_own_error(code, kind, words):
    engine = _engine(lambda r: httpx.Response(code, json={"error": {"message": "nope"}}))
    with pytest.raises(kind, match=words):
        await engine.transcribe(_wav(1), "audio/wav", None)


async def test_a_bad_request_passes_googles_own_words_on():
    msg = "The language si-LK is not supported by model long in location global."
    engine = _engine(lambda r: httpx.Response(400, json={"error": {"code": 400, "message": msg}}))
    with pytest.raises(EngineFailed, match="not supported by model long"):
        await engine.transcribe(_wav(1), "audio/wav", "si")


async def test_a_hung_google_is_didnt_answer():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(EngineFailed, match="didn't answer"):
        await _engine(handler).transcribe(_wav(1), "audio/wav", None)


async def test_the_token_never_reaches_a_log_or_an_error(caplog):
    caplog.set_level(logging.DEBUG)
    for code in (400, 401, 403, 404, 429, 500):
        engine = _engine(lambda r, c=code: httpx.Response(c, json={"error": {"message": "m"}}))
        with pytest.raises(Exception) as caught:
            await engine.transcribe(_wav(1), "audio/wav", None)
        assert TOKEN not in str(caught.value)
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(_engine(lambda r: _answer([])))


# ---------- credentials ----------

@pytest.fixture
def fresh_credentials(monkeypatch):
    google_cloud.reset_for_tests()
    yield
    google_cloud.reset_for_tests()


async def test_a_file_that_isnt_a_service_account_key_says_so(tmp_path, monkeypatch, fresh_credentials):
    key = tmp_path / "client.json"
    key.write_text(json.dumps({"web": {"client_id": "x.apps.googleusercontent.com", "client_secret": "SECRET"}}))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    with pytest.raises(google_cloud.GoogleAuthError) as caught:
        await google_cloud.access_token()
    assert "service-account key" in str(caught.value)
    assert "SECRET" not in str(caught.value) and str(tmp_path) not in str(caught.value)


async def test_no_credentials_anywhere_says_what_to_set(monkeypatch, fresh_credentials):
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    def none(**_):
        raise DefaultCredentialsError("nothing found")
    monkeypatch.setattr(google.auth, "default", none)
    with pytest.raises(google_cloud.GoogleAuthError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        await google_cloud.access_token()


async def test_a_token_is_kept_until_it_expires(monkeypatch, fresh_credentials):
    import google.auth

    class Creds:
        token, valid, refreshes = None, False, 0

        def refresh(self, _request):
            self.refreshes += 1
            self.token, self.valid = f"t{self.refreshes}", True
    creds = Creds()
    monkeypatch.setattr(google.auth, "default", lambda **_: (creds, "proj"))
    assert await google_cloud.access_token() == ("t1", "proj")
    assert await google_cloud.access_token() == ("t1", "proj")
    creds.valid = False
    assert await google_cloud.access_token() == ("t2", "proj")


async def test_google_cloud_project_names_the_billed_project_when_the_key_doesnt(monkeypatch, fresh_credentials):
    import google.auth

    class Creds:
        token, valid = "t", True
    monkeypatch.setattr(google.auth, "default", lambda **_: (Creds(), None))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-env")
    assert await google_cloud.access_token() == ("t", "from-env")


def test_the_engine_module_doesnt_import_google_auth_until_used():
    # A deployment without google-auth installed must still start: only the credentials call needs it.
    assert "google.auth" not in google_engine.__dict__
