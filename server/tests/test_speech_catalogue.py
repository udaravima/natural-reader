"""The speech models an admin can choose between: three sources, and a bad entry never stops anything."""
from __future__ import annotations

import logging

import pytest

from server.services.chat_options import ChatOptions
from server.speech import catalogue
from server.speech.catalogue import SpeechModel

RUN15 = "ab6154e9f312ce3f8803e624a84942213f523af3"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("SPEECH_MODELS_FILE", raising=False)
    monkeypatch.delenv("SPEECH_MODEL", raising=False)
    catalogue.reset_for_tests()
    yield
    catalogue.reset_for_tests()


def options(**over):
    return ChatOptions("ask", "default", **over)


def test_the_list_keeps_the_stock_sizes_and_adds_the_fine_tunes():
    models = catalogue.all_models({})
    assert {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"} <= set(models)
    assert {"whisper-small-ta", "whisper-small-si", "whisper-small-si-hf", "whisper-small-si-run15",
            "whisper-turbo"} <= set(models)
    assert models["small"].engine == "faster-whisper" and models["small"].languages == ()
    ta = models["whisper-small-ta"]
    assert (ta.engine, ta.repo, ta.languages) == ("converted-whisper", "vasista22/whisper-tamil-small", ("ta",))
    assert models["whisper-small-si-hf"].engine == "transformers-whisper"


def test_run15_is_pinned_to_its_own_commit_not_run12s():
    models = catalogue.all_models({})
    assert models["whisper-small-si-run15"].revision == RUN15
    assert models["whisper-small-si-hf"].revision != RUN15


def test_run12_is_offered_converted_and_unconverted_with_the_same_weights():
    models = catalogue.all_models({})
    fast, slow = models["whisper-small-si"], models["whisper-small-si-hf"]
    assert (fast.repo, fast.revision) == (slow.repo, slow.revision)
    assert (fast.engine, slow.engine) == ("converted-whisper", "transformers-whisper")


def test_every_built_in_model_is_pinned_except_the_stock_sizes():
    for model in catalogue.all_models({}).values():
        assert model.revision or model.repo == model.id, model.id


def test_a_file_adds_a_model_and_replaces_one_with_the_same_id(tmp_path):
    f = tmp_path / "speech.yaml"
    f.write_text("""
models:
  whisper-small-ta:
    engine: faster-whisper
    repo: example/tamil-ct2
    languages: [ta]
  my-model:
    # A comment, as in the owner's own list.
    engine: converted-whisper
    repo: example/whisper-si
    revision: abc123
    languages: [si]
    license: apache-2.0
    commercial: "yes"
    note: >
      A note folded
      over two lines.
""")
    models = catalogue.all_models({"SPEECH_MODELS_FILE": str(f)})
    assert models["whisper-small-ta"].repo == "example/tamil-ct2"
    mine = models["my-model"]
    assert (mine.revision, mine.commercial, mine.source) == ("abc123", "yes", "file")
    assert mine.note == "A note folded over two lines."
    assert "small" in models


def test_a_file_without_the_models_key_works_too(tmp_path):
    f = tmp_path / "speech.yaml"
    f.write_text("mine:\n  engine: faster-whisper\n  repo: example/ct2\n")
    assert catalogue.all_models({"SPEECH_MODELS_FILE": str(f)})["mine"].repo == "example/ct2"


@pytest.mark.parametrize("entry", [
    "mine: {engine: whisperx, repo: a/b}",                     # an engine nobody knows
    "mine: {engine: faster-whisper}",                          # no repo
    "mine: {engine: converted-whisper, repo: /etc/passwd}",    # only a Hub repo can be converted
    "mine: {engine: faster-whisper, repo: a/b, languages: [klingon]}",
    "mine: a string",
    "'Bad Id!': {engine: faster-whisper, repo: a/b}",
])
def test_a_bad_entry_is_a_warning_and_the_rest_stay(tmp_path, caplog, entry):
    f = tmp_path / "speech.yaml"
    f.write_text(f"{entry}\nfine: {{engine: faster-whisper, repo: a/b}}\n")
    caplog.set_level(logging.WARNING, logger="server.speech")
    models = catalogue.all_models({"SPEECH_MODELS_FILE": str(f)})
    assert "fine" in models and "mine" not in models and "Bad Id!" not in models
    assert any("left out" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("text", ["::: not yaml [", "- a\n- list\n", ""])
def test_a_broken_file_never_stops_anything(tmp_path, caplog, text):
    f = tmp_path / "speech.yaml"
    f.write_text(text)
    assert "small" in catalogue.all_models({"SPEECH_MODELS_FILE": str(f)})


def test_a_missing_file_is_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="server.speech")
    assert "small" in catalogue.all_models({"SPEECH_MODELS_FILE": "/nonexistent/speech.yaml"})
    assert any("SPEECH_MODELS_FILE" in r.getMessage() for r in caplog.records)


def test_an_admin_entry_joins_the_list_and_a_bad_one_doesnt():
    added = {"example-model": {"repo": "example/model", "revision": "abc", "engine": "converted-whisper",
                               "languages": ["si"], "label": "Mine"},
             "broken": {"repo": "x"}}
    models = catalogue.all_models({}, added)
    assert models["example-model"].source == "admin" and models["example-model"].label == "Mine"
    assert "broken" not in models and "small" in models


def test_wanted_admin_choice_wins_then_the_environment_then_small():
    env = {"SPEECH_MODEL": "medium"}
    assert catalogue.wanted(options(speech_model="whisper-small-ta"), env).id == "whisper-small-ta"
    assert catalogue.wanted(options(), env).id == "medium"
    assert catalogue.wanted(options(), {}).id == "small"


def test_speech_model_can_name_a_catalogue_id():
    assert catalogue.wanted(options(), {"SPEECH_MODEL": "whisper-small-si-run15"}).revision == RUN15


def test_speech_model_keeps_taking_a_repo_or_a_directory():
    for value in ("example/ct2-model", "/models/my-whisper"):
        m = catalogue.wanted(options(), {"SPEECH_MODEL": value})
        assert (m.id, m.repo, m.engine) == (value, value, "faster-whisper")


def test_a_saved_choice_that_is_gone_falls_back_with_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="server.speech")
    m = catalogue.wanted(options(speech_model="removed-from-the-file"), {"SPEECH_MODEL": "base"})
    assert m.id == "base"
    assert any("removed-from-the-file" in r.getMessage() for r in caplog.records)


def test_source_of_the_model_in_use():
    assert catalogue.source(options(speech_model="tiny"), {}) == "admin"
    assert catalogue.source(options(speech_model="gone"), {"SPEECH_MODEL": "base"}) == "env"
    assert catalogue.source(options(), {"SPEECH_MODEL": "base"}) == "env"
    assert catalogue.source(options(), {}) == "default"


def test_why_not_only_for_engines_that_need_torch(monkeypatch, tmp_path):
    monkeypatch.setattr(catalogue, "_installed", lambda name: False)
    stock = SpeechModel("x", "x", "faster-whisper", "x")
    unconverted = SpeechModel("y", "y", "transformers-whisper", "a/b", "r")
    convert = SpeechModel("z", "z", "converted-whisper", "a/b", "r")
    env = {"HF_HOME": str(tmp_path)}
    assert catalogue.why_not(stock, env) == ""
    assert "transformers" in catalogue.why_not(unconverted, env)
    assert "transformers" in catalogue.why_not(convert, env)


def test_a_model_already_converted_needs_no_torch(monkeypatch, tmp_path):
    from server.speech.convert import converted_dir
    monkeypatch.setattr(catalogue, "_installed", lambda name: False)
    m = SpeechModel("z", "z", "converted-whisper", "a/b", "r")
    env = {"HF_HOME": str(tmp_path)}
    out = converted_dir(m, env)
    out.mkdir(parents=True)
    (out / "model.bin").write_bytes(b"x")
    assert catalogue.why_not(m, env) == ""


def test_view_says_what_the_console_shows(monkeypatch):
    monkeypatch.setattr(catalogue, "_installed", lambda name: True)
    view = catalogue.view(catalogue.all_models({})["whisper-small-si-run15"], {})
    assert view["id"] == "whisper-small-si-run15" and view["languages"] == ["si"]
    assert view["language_names"] == ["Sinhala"] and view["removable"] is False and view["why_not"] == ""
    assert view["commercial"] == "check" and view["license"] == "apache-2.0"
