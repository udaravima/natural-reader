"""Is a speech model on this server's disk, and fetching one without loading it (spec 2026-11-04-speech-choices).
An admin's offer starts the download; people see a model only once it's here."""
from __future__ import annotations

import logging
import sys
import threading
import types

import pytest

from server.speech import disk
from server.speech.catalogue import SpeechModel

SMALL = SpeechModel("small", "Whisper small", "faster-whisper", "small")
TAMIL = SpeechModel("whisper-small-ta", "Tamil", "converted-whisper", "vasista22/whisper-tamil-small", "r1", ("ta",))


@pytest.fixture(autouse=True)
def _clean():
    disk.reset_for_tests()
    yield
    disk.reset_for_tests()


def _fake_utils(monkeypatch, download):
    utils = types.ModuleType("faster_whisper.utils")
    utils.download_model = download
    package = types.ModuleType("faster_whisper")
    package.utils = utils
    monkeypatch.setitem(sys.modules, "faster_whisper", package)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)


def test_a_converted_model_is_on_disk_once_its_model_bin_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert disk.on_disk(TAMIL) is False
    target = disk.converted_dir(TAMIL, {"HF_HOME": str(tmp_path)})
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(b"x")
    assert disk.on_disk(TAMIL, {"HF_HOME": str(tmp_path)}) is True


def test_a_faster_whisper_repo_is_checked_without_downloading(monkeypatch):
    calls = []

    def download(repo, local_files_only=False, revision=None, **_):
        calls.append((repo, local_files_only, revision))
        raise FileNotFoundError("not cached")
    _fake_utils(monkeypatch, download)
    assert disk.on_disk(SMALL) is False
    assert calls == [("small", True, None)]


def test_a_model_found_on_disk_is_not_checked_again(monkeypatch):
    calls = []
    _fake_utils(monkeypatch, lambda repo, **kw: calls.append(repo) or "/cache/small")
    assert disk.on_disk(SMALL) is True and disk.on_disk(SMALL) is True
    assert calls == ["small"]


def test_a_download_starts_once_per_model_while_it_runs(monkeypatch):
    release, started = threading.Event(), []

    def download(repo, local_files_only=False, revision=None, **_):
        started.append(repo)
        release.wait(5)
        return "/cache/small"
    _fake_utils(monkeypatch, download)
    disk.start_download(SMALL)
    disk.start_download(SMALL)   # still running: not a second one
    release.set()
    disk.wait_for_tests()
    assert started == ["small"]


def test_a_failed_download_is_logged_and_can_start_again(monkeypatch, caplog):
    attempts = []

    def download(repo, local_files_only=False, revision=None, **_):
        attempts.append(repo)
        raise OSError("no network")
    _fake_utils(monkeypatch, download)
    with caplog.at_level(logging.WARNING, logger="server.speech"):
        disk.start_download(SMALL)
        disk.wait_for_tests()
        disk.start_download(SMALL)
        disk.wait_for_tests()
    assert attempts == ["small", "small"]
    assert "OSError" in caplog.text
