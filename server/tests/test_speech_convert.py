"""Converting a Transformers-format Whisper model: once, in its own process, and every failure is a sentence."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from server.speech import convert
from server.speech.base import ConversionFailed
from server.speech.catalogue import SpeechModel

MODEL = SpeechModel("m", "M", "converted-whisper", "owner/name", "abc123")


def test_converted_dir_is_per_repo_and_revision(tmp_path):
    env = {"HF_HOME": str(tmp_path)}
    assert convert.converted_dir(MODEL, env) == tmp_path / "speech-converted" / "owner--name--abc123--int8"
    other = SpeechModel("m", "M", "converted-whisper", "owner/name", "def456")
    assert convert.converted_dir(other, env) != convert.converted_dir(MODEL, env)


def test_an_unpinned_model_lands_under_main(tmp_path):
    m = SpeechModel("m", "M", "converted-whisper", "owner/name")
    assert convert.converted_dir(m, {"HF_HOME": str(tmp_path)}).name == "owner--name--main--int8"


def test_a_finished_conversion_is_reused_without_a_process(tmp_path, monkeypatch):
    env = {"HF_HOME": str(tmp_path)}
    out = convert.converted_dir(MODEL, env)
    out.mkdir(parents=True)
    (out / "model.bin").write_bytes(b"x")
    monkeypatch.setattr(convert.subprocess, "run", lambda *a, **k: pytest.fail("no process needed"))
    assert convert.ensure_converted(MODEL, 2, env) == out


def test_conversion_runs_as_its_own_process_with_the_thread_limit(tmp_path, monkeypatch):
    env = {"HF_HOME": str(tmp_path), "PATH": "/bin"}
    seen = {}

    def run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], timeout=kw["timeout"], shell=kw.get("shell", False))
        out = cmd[-1]
        (tmp_path / "speech-converted").mkdir(exist_ok=True)
        import pathlib
        pathlib.Path(out).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(out) / "model.bin").write_bytes(b"x")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(convert.subprocess, "run", run)
    assert (convert.ensure_converted(MODEL, 3, env) / "model.bin").exists()
    assert seen["cmd"][:3] == [sys.executable, "-m", "server.speech.convert"]
    assert seen["cmd"][3:5] == ["owner/name", "abc123"]
    assert seen["env"]["OMP_NUM_THREADS"] == "3" and seen["shell"] is False
    assert seen["timeout"] == convert.CONVERT_TIMEOUT_S


def test_a_second_worker_finishing_first_is_not_an_error(tmp_path, monkeypatch):
    env = {"HF_HOME": str(tmp_path)}
    final = convert.converted_dir(MODEL, env)

    def run(cmd, **kw):
        import pathlib
        pathlib.Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(cmd[-1]) / "model.bin").write_bytes(b"mine")
        final.mkdir(parents=True)                # the other worker got there while we worked
        (final / "model.bin").write_bytes(b"theirs")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(convert.subprocess, "run", run)
    assert convert.ensure_converted(MODEL, 1, env) == final
    assert (final / "model.bin").read_bytes() == b"theirs"
    assert [p.name for p in final.parent.iterdir()] == [final.name]      # no scratch directory left behind


def test_a_failed_conversion_says_why_and_leaves_nothing(tmp_path, monkeypatch):
    env = {"HF_HOME": str(tmp_path)}
    monkeypatch.setattr(convert.subprocess, "run", lambda cmd, **k: SimpleNamespace(
        returncode=1, stderr="noise\nnoise\nRevisionNotFoundError: no such commit\n"))
    with pytest.raises(ConversionFailed) as e:
        convert.ensure_converted(MODEL, 1, env)
    assert "no such commit" in str(e.value)
    assert list((tmp_path / "speech-converted").iterdir()) == []


def test_a_conversion_that_hangs_is_stopped(tmp_path, monkeypatch):
    def run(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(convert.subprocess, "run", run)
    with pytest.raises(ConversionFailed):
        convert.ensure_converted(MODEL, 1, {"HF_HOME": str(tmp_path)})


def _info(files, model_type="whisper", sha="c" * 40, language=None):
    return SimpleNamespace(sha=sha, siblings=[SimpleNamespace(rfilename=f) for f in files],
                           config={"model_type": model_type} if model_type else None,
                           card_data=SimpleNamespace(language=language) if language else None)


class FakeApi:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def model_info(self, repo, revision=None, timeout=None):
        self.calls.append((repo, revision, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_inspect_a_transformers_repo_pins_the_commit_and_reads_the_language(monkeypatch):
    api = FakeApi(_info(["config.json", "model.safetensors"], language="si"))
    monkeypatch.setattr(convert, "_api", lambda: api)
    facts = convert.inspect_repo("owner/name", None)
    assert (facts.revision, facts.engine, facts.languages) == ("c" * 40, "converted-whisper", ("si",))
    assert api.calls[0][2] == convert.HUB_TIMEOUT_S


def test_inspect_a_pytorch_only_repo(monkeypatch):
    monkeypatch.setattr(convert, "_api", lambda: FakeApi(_info(["pytorch_model.bin"], language=["ta"])))
    facts = convert.inspect_repo("owner/name", "main")
    assert facts.engine == "converted-whisper" and facts.languages == ("ta",)


def test_inspect_a_ctranslate2_repo_needs_no_conversion(monkeypatch):
    monkeypatch.setattr(convert, "_api", lambda: FakeApi(_info(["model.bin", "vocabulary.json"], model_type=None)))
    assert convert.inspect_repo("owner/name", None).engine == "faster-whisper"


def test_inspect_many_languages_means_every_language(monkeypatch):
    monkeypatch.setattr(convert, "_api", lambda: FakeApi(_info(["model.safetensors"], language=["en", "fr"])))
    assert convert.inspect_repo("owner/name", None).languages == ()


def test_inspect_refuses_what_isnt_whisper(monkeypatch):
    monkeypatch.setattr(convert, "_api", lambda: FakeApi(_info(["model.safetensors"], model_type="bert")))
    with pytest.raises(convert.NotWhisper):
        convert.inspect_repo("owner/name", None)


def test_inspect_refuses_a_repo_with_no_weights(monkeypatch):
    monkeypatch.setattr(convert, "_api", lambda: FakeApi(_info(["README.md"])))
    with pytest.raises(convert.NotWhisper):
        convert.inspect_repo("owner/name", None)


def test_inspect_turns_hub_errors_into_two_kinds(monkeypatch):
    from huggingface_hub.errors import RepositoryNotFoundError, RevisionNotFoundError
    response = SimpleNamespace(status_code=404, headers={}, request=None, url="https://hf.example.com")
    for error in (RepositoryNotFoundError("x", response=response), RevisionNotFoundError("x", response=response)):
        monkeypatch.setattr(convert, "_api", lambda: FakeApi(error))
        with pytest.raises(convert.NotOnHub):
            convert.inspect_repo("owner/name", "abc")
    monkeypatch.setattr(convert, "_api", lambda: FakeApi(ConnectionError("offline")))
    with pytest.raises(convert.HubUnreachable):
        convert.inspect_repo("owner/name", None)
