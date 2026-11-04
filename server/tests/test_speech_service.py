"""Task 5: the service rules around the engines."""
from __future__ import annotations

import asyncio
import logging
import os

import pytest

from server.speech import service
from server.speech.base import EngineNotReady, EngineStatus, Transcript
from server.speech.catalogue import SpeechModel
from server.speech.local_whisper import LocalWhisper
from server.speech.openai_engine import OpenAICompatible

ENV_KEYS = ("SPEECH_ENGINE", "SPEECH_MODEL", "SPEECH_URL", "SPEECH_API_KEY", "SPEECH_CPU_THREADS")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    service.reset_for_tests()
    yield
    service.reset_for_tests()


class Slow:
    """A stand-in engine that holds until released."""
    model, where = "m", "this server"

    def __init__(self, name="local"):
        self.name = name
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.finished = 0

    async def transcribe(self, audio, mime, language):
        self.started.set()
        await self.release.wait()
        self.finished += 1
        return Transcript("hi", "en", 2.0)

    async def status(self):
        return EngineStatus(True, "ready", "")

    def warm(self):
        pass


async def test_service_one_per_person_and_busy(monkeypatch):
    eng = Slow()
    service._transcriber = eng
    monkeypatch.setattr(service, "LOCAL_WAIT_S", 0.05)
    first = asyncio.create_task(service.transcribe_for("u1", b"a", "audio/webm", None))
    await eng.started.wait()
    with pytest.raises(service.AlreadyRunning):
        await service.transcribe_for("u1", b"a", "audio/webm", None)
    with pytest.raises(service.Busy):
        await service.transcribe_for("u2", b"a", "audio/webm", None)
    eng.release.set()
    assert (await first).text == "hi"
    # Both are free again.
    assert (await service.transcribe_for("u1", b"a", "audio/webm", None)).text == "hi"
    assert (await service.transcribe_for("u2", b"a", "audio/webm", None)).text == "hi"


async def test_remote_engine_has_no_process_wide_slot():
    eng = Slow("openai")
    service._transcriber = eng
    a = asyncio.create_task(service.transcribe_for("u1", b"a", "audio/webm", None))
    b = asyncio.create_task(service.transcribe_for("u2", b"a", "audio/webm", None))
    await asyncio.sleep(0.05)
    eng.release.set()
    assert [(await a).text, (await b).text] == ["hi", "hi"]


async def test_a_cancelled_request_keeps_the_local_slot_until_the_work_ends(monkeypatch):
    eng = Slow()
    service._transcriber = eng
    monkeypatch.setattr(service, "LOCAL_WAIT_S", 0.05)
    t = asyncio.create_task(service.transcribe_for("u1", b"a", "audio/webm", None))
    await eng.started.wait()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    with pytest.raises(service.Busy):  # the thread is still running
        await service.transcribe_for("u2", b"a", "audio/webm", None)
    eng.release.set()
    await asyncio.sleep(0.05)
    assert eng.finished == 1
    assert (await service.transcribe_for("u2", b"a", "audio/webm", None)).text == "hi"


def test_the_recording_limits():
    # The size is refused by the route, before the service is called (test_speech_router.py).
    assert (service.MAX_AUDIO_BYTES, service.MAX_AUDIO_SECONDS) == (10 * 1024 * 1024, 125)


async def test_the_log_line_has_no_text(caplog):
    service._transcriber = Slow("openai")
    service._transcriber.release.set()
    caplog.set_level(logging.INFO, logger="server.speech")
    await service.transcribe_for("u7", b"a", "audio/webm", None)
    msgs = [r.getMessage() for r in caplog.records if r.name == "server.speech"]
    assert len(msgs) == 1
    assert msgs[0].startswith("transcribed user=u7 engine=openai model=m seconds=2.0 language=en elapsed_ms=")


async def test_service_engine_from_env(monkeypatch, caplog):
    eng = service.get_transcriber()
    assert isinstance(eng, LocalWhisper) and eng.model == "small" and eng.name == "local"
    assert service.get_transcriber() is eng  # one per process

    service.reset_for_tests()
    eng = service.get_transcriber({"SPEECH_MODEL": "large-v3-turbo", "SPEECH_CPU_THREADS": "3"})
    assert eng.model == "large-v3-turbo" and eng.cpu_threads == 3

    service.reset_for_tests()
    eng = service.get_transcriber({"SPEECH_ENGINE": "openai"})  # no SPEECH_URL: built, not raised
    assert isinstance(eng, OpenAICompatible)
    st = await eng.status()
    assert (st.ready, st.state) == (False, "error") and "SPEECH_URL" in st.detail
    with pytest.raises(EngineNotReady) as e:
        await eng.transcribe(b"a", "audio/webm", None)
    assert "SPEECH_URL" in e.value.detail

    service.reset_for_tests()
    eng = service.get_transcriber({"SPEECH_ENGINE": "openai", "SPEECH_URL": "https://stt.example.com/v1",
                                   "SPEECH_MODEL": "whisper-1", "SPEECH_API_KEY": "k"})
    assert (eng.name, eng.model, eng.where) == ("openai", "whisper-1", "stt.example.com")

    service.reset_for_tests()
    caplog.set_level(logging.WARNING, logger="server.speech")
    first = service.get_transcriber({"SPEECH_ENGINE": "whisperx"})
    assert isinstance(first, LocalWhisper)
    assert service.get_transcriber({"SPEECH_ENGINE": "whisperx"}) is first
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "whisperx" in warnings[0].getMessage()


def test_cpu_threads_default_and_bad_value(monkeypatch):
    monkeypatch.setattr(service.os, "cpu_count", lambda: 8)
    assert service._cpu_threads({}) == 4
    monkeypatch.setattr(service.os, "cpu_count", lambda: 1)
    assert service._cpu_threads({}) == 1
    assert service._cpu_threads({"SPEECH_CPU_THREADS": "abc"}) == 1


class Failing(Slow):
    async def transcribe(self, audio, mime, language):
        raise RuntimeError("engine blew up")


@pytest.mark.parametrize("name", ["local", "openai"])
async def test_an_engine_failure_releases_the_person_and_the_slot(name, monkeypatch):
    service._transcriber = Failing(name)
    monkeypatch.setattr(service, "LOCAL_WAIT_S", 0.05)
    with pytest.raises(RuntimeError):
        await service.transcribe_for("u1", b"a", "audio/webm", None)
    assert "u1" not in service._running
    with pytest.raises(RuntimeError):  # same person again: not AlreadyRunning
        await service.transcribe_for("u1", b"a", "audio/webm", None)
    with pytest.raises(RuntimeError):  # another person: not Busy
        await service.transcribe_for("u2", b"a", "audio/webm", None)
    ok = Slow(name)
    ok.release.set()
    service._transcriber = ok
    assert (await service.transcribe_for("u1", b"a", "audio/webm", None)).text == "hi"


# ---- final fix wave: I6 (threads shared between workers) and M3 (a missing package can't take the backend down) ----

def test_cpu_threads_default_is_shared_between_workers(monkeypatch):
    monkeypatch.setattr(service.os, "cpu_count", lambda: 16)
    assert service._cpu_threads({}) == 8
    assert service._cpu_threads({"WORKERS": "2"}) == 4
    assert service._cpu_threads({"WORKERS": "4"}) == 2
    assert service._cpu_threads({"WORKERS": "64"}) == 1        # never below one
    assert service._cpu_threads({"WORKERS": "nope"}) == 8      # a bad value is one worker, as run.py reads it
    assert service._cpu_threads({"WORKERS": "0"}) == 8
    assert service._cpu_threads({"WORKERS": "4", "SPEECH_CPU_THREADS": "6"}) == 6   # an explicit value wins as is


@pytest.mark.parametrize("engine, module", [("local", "server.speech.local_whisper"),
                                            ("openai", "server.speech.openai_engine")])
async def test_a_missing_package_gives_a_stand_in_that_says_so(monkeypatch, engine, module):
    import builtins
    import sys

    real_import = builtins.__import__

    def failing(name, *a, **k):
        if name in ("faster_whisper", "av", "numpy"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *a, **k)
    for m in ("server.speech.local_whisper", "server.speech.openai_engine", "server.speech.audio"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(builtins, "__import__", failing)
    eng = service.get_transcriber({"SPEECH_ENGINE": engine})        # must not raise
    st = await eng.status()
    assert (st.ready, st.state) == (False, "error")
    assert "pip install -r requirements.txt" in st.detail and ("faster_whisper" in st.detail or "av" in st.detail)
    from server.speech.base import EngineUnavailable
    with pytest.raises(EngineNotReady) as e:
        await eng.transcribe(b"a", "audio/webm", None)
    assert isinstance(e.value, EngineUnavailable)
    eng.warm()                                                      # a no-op, not an error


def test_the_routes_and_the_service_import_without_the_audio_packages():
    import subprocess
    import sys
    code = ("import sys\n"
            "for n in ('faster_whisper', 'av', 'numpy'): sys.modules[n] = None\n"
            "import server.speech.base, server.speech.languages, server.speech.service, server.routers.speech\n"
            "print('ok')\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(__import__('pathlib').Path(__file__).parents[2]))
    assert out.stdout.strip() == "ok", out.stderr[-800:]


# ---------- the deployment's chosen model (spec 2026-11-02-speech-model-choice) ----------

TINY = SpeechModel("tiny", "Whisper tiny", "faster-whisper", "tiny")
TAMIL = SpeechModel("ta", "Tamil", "converted-whisper", "a/ta", "r", ("ta",))
UNCONVERTED = SpeechModel("si", "Sinhala", "transformers-whisper", "a/si", "r", ("si",))


def test_a_chosen_model_replaces_this_workers_engine_once():
    first = service.get_transcriber({}, TINY)
    assert first.spec == TINY
    assert service.get_transcriber({}, TINY) is first
    second = service.get_transcriber({}, TAMIL)
    assert second is not first and second.spec == TAMIL and second.languages == ("ta",)
    assert service.get_transcriber({}) is second       # a caller with no opinion keeps what's running


def test_the_unconverted_engine_is_built_for_its_kind():
    from server.speech.hf_whisper import TransformersWhisper
    assert isinstance(service.get_transcriber({}, UNCONVERTED), TransformersWhisper)


def test_a_newly_built_engine_has_no_retry_clock_of_its_own():
    service._attempt_at["tiny"] = 123.0
    service.get_transcriber({}, TINY)
    assert "tiny" not in service._attempt_at


def test_a_remote_engine_ignores_the_choice():
    eng = service.get_transcriber({"SPEECH_ENGINE": "openai"}, TINY)
    assert service.get_transcriber({"SPEECH_ENGINE": "openai"}, TAMIL) is eng


def test_speech_model_can_name_a_catalogue_entry():
    eng = service.get_transcriber({"SPEECH_MODEL": "whisper-small-ta"})
    assert eng.spec.engine == "converted-whisper" and eng.languages == ("ta",)


async def test_a_recording_in_progress_finishes_on_the_engine_it_started_on():
    old = Slow()
    service._transcriber = old
    old.spec = TINY
    first = asyncio.create_task(service.transcribe_for("u1", b"a", "audio/webm", None))
    await old.started.wait()
    service.get_transcriber({}, TAMIL)                # the admin switches while it runs
    old.release.set()
    assert (await first).text == "hi" and old.finished == 1


# ---------- several models per worker (spec 2026-11-04-speech-choices-per-person, Part 2) ----------

SI = SpeechModel("si", "Sinhala", "converted-whisper", "a/si", "r", ("si",))
TA = SpeechModel("ta", "Tamil", "converted-whisper", "a/ta", "r", ("ta",))
EN = SpeechModel("small.en", "English", "faster-whisper", "small.en", languages=("en",))


class FakeLocal:
    """A local engine on one model that loads when warmed (after `load_s`), hears with its id as the text, and can
    be held mid-transcription."""
    name, where = "local", "this server"

    def __init__(self, spec, ready=True, load_s=0.0, fails=False):
        self.spec, self.model, self.languages = spec, spec.id, spec.languages
        self.state = "ready" if ready else "loading"
        self.load_s, self.fails = load_s, fails
        self.hold = None
        self.started = asyncio.Event()
        self.finished = 0

    async def status(self):
        return EngineStatus(self.state == "ready", self.state, "broken" if self.state == "error" else "")

    def warm(self):
        if self.state != "loading":
            return
        asyncio.get_running_loop().call_later(self.load_s, setattr, self, "state", "error" if self.fails else "ready")

    async def transcribe(self, audio, mime, language):
        if self.state != "ready":
            raise EngineNotReady("The speech model is still loading")
        self.started.set()
        if self.hold is not None:
            await self.hold.wait()
        self.finished += 1
        return Transcript(self.spec.id, language or "en", 1.0)


@pytest.fixture
def fakes(monkeypatch):
    built = {}

    def build(spec, env):
        built[spec.id] = FakeLocal(spec)
        return built[spec.id]
    monkeypatch.setattr(service, "_build_local", build)
    dropped = []
    monkeypatch.setattr(service, "_release_memory", lambda: dropped.append(True))
    return built, dropped


def _pooled():
    return list(service._pool)


def test_up_to_the_limit_of_models_stay_loaded_and_the_least_recently_used_goes(fakes):
    _, dropped = fakes
    env = {"SPEECH_MODELS_LOADED": "2"}
    for spec in (TINY, SI, TA):
        service.get_transcriber(env, spec, default=TINY)
    assert _pooled() == ["tiny", "ta"] and len(dropped) == 1


def test_the_default_keeps_its_place(fakes):
    env = {"SPEECH_MODELS_LOADED": "2"}
    service.get_transcriber(env, TINY)
    for spec in (SI, TA, EN, SI):
        service.get_transcriber(env, spec, default=TINY)
        assert "tiny" in _pooled() and len(_pooled()) == 2


def test_with_one_place_models_take_turns(fakes):
    env = {"SPEECH_MODELS_LOADED": "1"}
    service.get_transcriber(env, TINY)
    service.get_transcriber(env, SI, default=TINY)
    assert _pooled() == ["si"]


async def test_a_model_in_use_is_never_dropped(fakes, monkeypatch):
    monkeypatch.setenv("SPEECH_MODELS_LOADED", "2")   # transcribe_for reads the limit from the environment
    env = os.environ
    service.get_transcriber(env, TINY)
    si = service.get_transcriber(env, SI, default=TINY)
    si.hold = asyncio.Event()
    running = asyncio.create_task(service.transcribe_for("u1", b"a", "audio/wav", "si", engine=si))
    await si.started.wait()
    service.get_transcriber(env, TA, default=TINY)
    assert set(_pooled()) == {"tiny", "si", "ta"}      # over the limit while Sinhala is heard
    si.hold.set()
    assert (await running).text == "si"
    assert _pooled() == ["tiny", "ta"]                 # Sinhala goes once it is no longer heard


def test_the_limit_defaults_to_3_and_a_bad_value_warns(caplog):
    assert service.models_loaded({}) == 3
    assert service.models_loaded({"SPEECH_MODELS_LOADED": "1"}) == 1
    assert service.models_loaded({"SPEECH_MODELS_LOADED": "4"}) == 4
    with caplog.at_level(logging.WARNING, logger="server.speech"):
        for raw in ("0", "5", "x"):
            assert service.models_loaded({"SPEECH_MODELS_LOADED": raw}) == 3
    assert "SPEECH_MODELS_LOADED" in caplog.text


async def test_each_clip_is_heard_by_the_engine_its_request_resolved(fakes):
    si = service.get_transcriber({}, SI, default=TINY)
    si.hold = asyncio.Event()
    first = asyncio.create_task(service.transcribe_for("u1", b"a", "audio/wav", "si", engine=si))
    await si.started.wait()
    ta = service.get_transcriber({}, TA, default=TINY)   # someone else, on another model, while Sinhala is heard
    second = asyncio.create_task(service.transcribe_for("u2", b"b", "audio/wav", "ta", engine=ta))
    si.hold.set()
    assert ((await first).text, (await second).text) == ("si", "ta")


async def test_a_clip_for_a_model_still_loading_waits_for_it(monkeypatch):
    monkeypatch.setattr(service, "_release_memory", lambda: None)
    loading = FakeLocal(SI, ready=False, load_s=0.3)
    assert (await service.transcribe_for("u1", b"a", "audio/wav", "si", engine=loading)).text == "si"


async def test_a_model_that_fails_to_load_is_unavailable_not_busy(monkeypatch):
    from server.speech.base import EngineUnavailable
    monkeypatch.setattr(service, "_release_memory", lambda: None)
    broken = FakeLocal(SI, ready=False, load_s=0.1, fails=True)
    with pytest.raises(EngineUnavailable):
        await service.transcribe_for("u1", b"a", "audio/wav", "si", engine=broken)


async def test_the_log_line_names_the_model(caplog, monkeypatch):
    monkeypatch.setattr(service, "_release_memory", lambda: None)
    with caplog.at_level(logging.INFO, logger="server.speech"):
        await service.transcribe_for("u1", b"a", "audio/wav", None, engine=FakeLocal(TINY))
    assert "model=tiny" in caplog.text


def test_switching_to_a_remote_engine_lets_the_local_models_go(fakes):
    _, dropped = fakes
    service.get_transcriber({}, TINY)
    service.get_transcriber({}, SI, default=TINY)
    service.get_transcriber({"SPEECH_ENGINE": "openai", "SPEECH_URL": "http://x", "SPEECH_MODEL": "m"}, engine="openai")
    assert _pooled() == [] and dropped


# ---------- Google recognizers, one per language (spec 2026-11-04-google-recognizers) ----------

from types import SimpleNamespace  # noqa: E402

from server.speech.google_engine import GoogleSpeech  # noqa: E402

G_SI = "projects/p-1/locations/asia-southeast1/recognizers/asr-si"
G_TA = "projects/p-1/locations/asia-southeast1/recognizers/asr-ta-en"
ENV_NAME = "projects/p-1/locations/global/recognizers/from-env"


def _options(*names, engine="google"):
    return SimpleNamespace(google_recognizers=tuple(names), speech_engine=engine)


def _heard(engines, languages):
    for engine, langs in zip(engines, languages):
        engine.languages = tuple(langs)   # as status() leaves them after reading the recognizer
    return engines


def test_the_admins_recognizers_win_over_the_environments():
    env = {"GOOGLE_SPEECH_RECOGNIZER": ENV_NAME}
    assert service.recognizer_names(_options(G_TA, G_SI), env) == (G_TA, G_SI)
    assert service.recognizer_source(_options(G_TA, G_SI), env) == "admin"
    assert service.recognizer_names(_options(), env) == (ENV_NAME,)
    assert service.recognizer_source(_options(), env) == "env"
    assert service.recognizer_names(_options(), {}) == ()
    assert service.recognizer_source(_options(), {}) == "none"


def test_a_language_goes_to_the_first_recognizer_that_lists_it():
    ta, si = _heard(service.google_engines((G_TA, G_SI), {}), [("ta", "en"), ("si",)])
    assert service.google_for([ta, si], "si") is si
    assert service.google_for([ta, si], "ta") is ta
    assert service.google_for([ta, si], "en") is ta
    assert service.google_for([si, ta], "en") is ta      # the first that LISTS it, not the first


def test_auto_and_an_unlisted_language_go_to_the_first():
    ta, si = _heard(service.google_engines((G_TA, G_SI), {}), [("ta", "en"), ("si",)])
    assert service.google_for([ta, si], None) is ta
    assert service.google_for([ta, si], "fr") is ta
    unread = service.google_engines(("projects/p-1/locations/us/recognizers/never-read",), {})[0]
    assert unread.languages == ()                                      # never answered: lists nothing yet
    assert service.google_for([ta, unread], "si") is ta


def test_one_engine_is_kept_per_recognizer():
    first = service.google_engines((G_TA, G_SI), {})
    again = service.google_engines((G_SI, G_TA), {})
    assert again[0] is first[1] and again[1] is first[0]
    assert all(isinstance(e, GoogleSpeech) for e in first)
    assert [e.recognizer for e in first] == [G_TA, G_SI]


def test_google_can_be_chosen_once_a_recognizer_is_ticked():
    assert service.engine_needs("google", {}) != ""
    assert service.engine_needs("google", {}, _options(G_SI)) == ""
    assert service.engine_needs("google", {"GOOGLE_SPEECH_RECOGNIZER": ENV_NAME}) == ""
    assert service.engine_needs("google", {"GOOGLE_SPEECH_RECOGNIZER": "projects/p/locations/a.b/recognizers/r"}) != ""
    assert service.chosen_engine(_options(G_SI), {}) == "google"
    assert service.chosen_engine(_options(), {}) == "local"            # nothing ticked, nothing set: Whisper
    google = {e["id"]: e for e in service.engines({}, _options(G_SI))}["google"]
    assert google["available"] is True


def test_the_recognizer_list_is_offered_once_google_is_configured():
    assert not service.google_offered(_options(), {})
    assert service.google_offered(_options(), {"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json"})
    assert service.google_offered(_options(), {"GOOGLE_SPEECH_RECOGNIZER": ENV_NAME})
    assert service.google_offered(_options(G_SI), {})


def test_switching_recognizers_replaces_the_current_engine():
    first = service.get_transcriber({}, engine="google", recognizers=(G_TA, G_SI))
    assert first.recognizer == G_TA
    assert service.get_transcriber({}, engine="google", recognizers=(G_TA, G_SI)) is first
    second = service.get_transcriber({}, engine="google", recognizers=(G_SI,))
    assert second.recognizer == G_SI
    assert service.get_transcriber({}, engine="google", recognizers=()).recognizer == ""   # a status, not a crash
