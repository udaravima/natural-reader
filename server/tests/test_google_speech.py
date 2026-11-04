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


async def test_chirp_3_picks_among_its_languages_so_auto_isnt_only_the_first():
    """Live 2026-11-04: chirp_3 given ta-IN and en-GB heard an English clip as en-GB and a Tamil one as ta-IN."""
    engine = _engine(lambda r: httpx.Response(200, json={**RECOGNIZER, "model": "chirp_3",
                                                         "languageCodes": ["ta-IN", "en-GB"]}))
    await engine.status()
    assert engine.auto_hears is None and engine.languages == ("ta", "en")


async def test_a_refused_clip_logs_googles_own_words(caplog):
    """Live 2026-11-04: a chirp_3 recognizer in asia-southeast1 reads fine but every clip is refused; only Google's
    message says why, so the log carries it (it holds no credential)."""
    words = ("Permission denied for project 1 on model chirp_3 locale en-GB. It is no longer generally available in "
             "region asia-southeast1. Try a different region, model, or locale.")

    def handler(request):
        if request.url.path.endswith(":recognize"):
            return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED", "message": words}})
        return httpx.Response(200, json=RECOGNIZER)
    engine = _engine(handler)
    with caplog.at_level(logging.WARNING, logger="server.speech"), pytest.raises(EngineUnavailable):
        await engine.transcribe(_wav(1), "audio/wav", "en")
    assert "no longer generally available in region asia-southeast1" in caplog.text
    assert TOKEN not in caplog.text


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


# ---------- recognizers listed from Google, for the console (spec 2026-11-04-google-recognizers) ----------

@pytest.mark.parametrize("name", [
    "projects/p/locations/evil.example#/recognizers/r",   # the location becomes the host the token goes to
    "projects/p/locations/a.b/recognizers/r",
    "projects/p/locations/x?y/recognizers/r",
    "projects/p/locations/u@h/recognizers/r",
    "projects/P/locations/global/recognizers/r",
    "projects/p/locations/global/recognizers/R_1",
    "projects/p/locations/global/recognizers/r/extra",
])
def test_a_name_whose_location_could_pick_another_host_is_refused(name):
    assert not google_engine.is_recognizer_name(name)
    assert google_engine.split_name(name) is None
    assert GoogleSpeech(name).endpoint == "https://speech.googleapis.com"   # never a host built from it


@pytest.mark.parametrize("name, parts", [
    ("projects/example-project-123456/locations/asia-southeast1/recognizers/asr-si-en",
     ("example-project-123456", "asia-southeast1", "asr-si-en")),
    ("projects/123456789012/locations/global/recognizers/asr-si-ta", ("123456789012", "global", "asr-si-ta")),
])
def test_real_names_pass_and_split(name, parts):
    assert google_engine.is_recognizer_name(name)
    assert google_engine.split_name(name) == parts


def _recognizer(project, location, rid, model, codes, state="ACTIVE"):
    return {"name": f"projects/{project}/locations/{location}/recognizers/{rid}", "model": model,
            "languageCodes": codes, "state": state}


NUMBERS = {"p-1": "111"}   # the fake Google's project id -> number


def _google(locations_pages, recognizers, refused=()):
    """A fake Speech v2: locations in pages; recognizers per location, each a list of pages; `refused` locations
    answer 403. Records every URL asked for and every Authorization header."""
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        token = request.url.params.get("pageToken")
        path = request.url.path
        if path.endswith("/locations"):
            page = int(token or 0)
            asked = path.split("/projects/")[1].split("/")[0]
            number = NUMBERS.get(asked, asked)   # Google names a location with the project's number
            body = {"locations": [{"name": f"projects/{number}/locations/{loc}", "locationId": loc}
                                  for loc in locations_pages[page]]}
            if page + 1 < len(locations_pages):
                body["nextPageToken"] = str(page + 1)
            return httpx.Response(200, json=body)
        location = path.split("/locations/")[1].split("/")[0]
        if location in refused:
            return httpx.Response(403, json={"error": {"code": 403, "message": "Permission denied on resource"}})
        pages = recognizers.get(location, [[]])
        page = int(token or 0)
        body = {"recognizers": pages[page]}
        if page + 1 < len(pages):
            body["nextPageToken"] = str(page + 1)
        return httpx.Response(200, json=body)
    return handler, seen


async def test_listing_reads_every_location_and_every_page():
    si = _recognizer("p-1", "asia-southeast1", "asr-si", "chirp_2", ["si-LK"])
    ta = _recognizer("p-1", "asia-southeast1", "asr-ta-en", "chirp_3", ["ta-IN", "en-GB"])
    gone = _recognizer("p-1", "asia-southeast1", "old", "long", ["en-US"], state="DELETED")
    us = _recognizer("p-1", "us", "asr-en", "chirp_3", ["en-US"])
    handler, seen = _google([["asia-northeast1", "asia-southeast1"], ["global", "us"]],
                            {"asia-southeast1": [[si, gone], [ta]], "us": [[us]]})
    listing = await google_engine.list_recognizers(["p-1"], transport=httpx.MockTransport(handler), token=_token)
    assert listing.problems == []
    by_id = {r["id"]: r for r in listing.recognizers}
    assert sorted(by_id) == ["asr-en", "asr-si", "asr-ta-en"]           # DELETED left out, both pages read
    assert by_id["asr-ta-en"] == {
        "name": ta["name"], "id": "asr-ta-en", "project": "p-1", "location": "asia-southeast1", "model": "chirp_3",
        "codes": ["ta-IN", "en-GB"], "languages": ["ta", "en"]}
    hosts = {url.split("/v2/")[0] for url, _ in seen}
    assert "https://asia-southeast1-speech.googleapis.com" in hosts and "https://us-speech.googleapis.com" in hosts
    assert "https://speech.googleapis.com" in hosts                     # locations, and global's recognizers
    assert all(auth == f"Bearer {TOKEN}" for _, auth in seen)


async def test_listing_without_projects_lists_the_keys_own():
    handler, seen = _google([["global"]], {"global": [[_recognizer("p-1", "global", "r", "long", ["en-US"])]]})
    listing = await google_engine.list_recognizers(None, transport=httpx.MockTransport(handler), token=_token)
    assert [r["id"] for r in listing.recognizers] == ["r"]
    assert all("/projects/p-1/" in url for url, _ in seen)


async def test_listing_reports_googles_refusal_in_its_words():
    ok = _recognizer("p-1", "global", "r", "long", ["en-US"])
    handler, _ = _google([["eu", "global"]], {"global": [[ok]]}, refused=("eu",))
    listing = await google_engine.list_recognizers(["p-1"], transport=httpx.MockTransport(handler), token=_token)
    assert [r["id"] for r in listing.recognizers] == ["r"]
    assert len(listing.problems) == 1
    assert "eu" in listing.problems[0] and "Permission denied" in listing.problems[0]
    assert TOKEN not in listing.problems[0]


async def test_listing_with_credentials_that_dont_work_is_a_problem_not_a_crash():
    async def broken():
        raise google_cloud.GoogleAuthError("No Google credentials: set GOOGLE_APPLICATION_CREDENTIALS")
    listing = await google_engine.list_recognizers(None, transport=httpx.MockTransport(lambda r: None), token=broken)
    assert listing.recognizers == [] and listing.problems == ["No Google credentials: set GOOGLE_APPLICATION_CREDENTIALS"]


async def test_label_names_the_recognizer_and_its_model():
    engine = _engine(lambda r: httpx.Response(200, json=RECOGNIZER))
    assert (engine.id, engine.label) == ("r-1", "r-1")            # its model isn't known until it's read
    await engine.status()
    assert engine.label == "r-1 (long)"


async def test_a_project_asked_for_by_id_and_by_number_is_listed_once():
    """Google writes a recognizer's project as its number; the console asks for the key's project by id and for a
    ticked one's by number. They are one project: listed once, and the id is mapped to the number."""
    row = _recognizer("111", "global", "r", "long", ["en-US"])
    handler, seen = _google([["global"]], {"global": [[row]]})
    listing = await google_engine.list_recognizers(["111"], transport=httpx.MockTransport(handler), token=_token)
    assert [r["name"] for r in listing.recognizers] == ["projects/111/locations/global/recognizers/r"]
    assert sum(url.endswith("/locations") for url, _ in seen) == 1
    assert listing.projects == {"p-1": "111"}
    assert google_engine.canonical("projects/p-1/locations/global/recognizers/r", listing) \
        == "projects/111/locations/global/recognizers/r"
    assert google_engine.canonical("projects/other/locations/global/recognizers/r", listing) \
        == "projects/other/locations/global/recognizers/r"
