"""Is a speech model on this server's disk, and fetching one without loading it (spec
2026-11-04-speech-choices-per-person). An admin offering a model to people starts its download here; people see it
only once it's on disk, so nobody waits on a multi-gigabyte download in the middle of a sentence.

The files go where the engines look for them: HF_HOME's cache for a faster-whisper or Transformers repo, and the
converted directory (speech/convert.py) for a converted fine-tune. Nothing is loaded into memory here. Every worker
shares HF_HOME, so the worker that took the admin's click downloads for all of them.

Nothing heavy is imported at module level: the routes import this, and must start without the audio packages.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path

from .catalogue import CONVERTED, FASTER_WHISPER, TRANSFORMERS, SpeechModel
from .convert import converted_dir, ensure_converted

logger = logging.getLogger("server.speech")

__all__ = ["converted_dir", "on_disk", "start_download"]

_seen: set[tuple[str, str | None]] = set()   # (id, revision) found on disk: a model doesn't leave, so never again
_running: set[str] = set()                    # ids being downloaded by this process
_threads: list[threading.Thread] = []
_lock = threading.Lock()


def reset_for_tests() -> None:
    _seen.clear()
    _running.clear()
    _threads.clear()


def wait_for_tests() -> None:
    for thread in list(_threads):
        thread.join(5)


def _faster_whisper_cached(model: SpeechModel) -> bool:
    if Path(model.repo).is_dir():   # a deployer's local directory
        return True
    try:
        from faster_whisper.utils import download_model
        download_model(model.repo, local_files_only=True, revision=model.revision)
    except Exception:  # noqa: BLE001 - not cached (huggingface_hub's error), or the package isn't installed
        return False
    return True


def _repo_cached(model: SpeechModel) -> bool:
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(model.repo, revision=model.revision, local_files_only=True)
    except Exception:  # noqa: BLE001 - not cached, or the package isn't installed
        return False
    return True


def on_disk(model: SpeechModel, env: Mapping[str, str] = os.environ) -> bool:
    """Whether the model's files are here, so loading it reads the disk and downloads nothing."""
    key = (model.id, model.revision)
    if key in _seen:
        return True
    if model.engine == CONVERTED:
        found = (converted_dir(model, env) / "model.bin").exists()
    elif model.engine == FASTER_WHISPER:
        found = _faster_whisper_cached(model)
    elif model.engine == TRANSFORMERS:
        found = _repo_cached(model)
    else:
        found = False
    if found:
        _seen.add(key)
    return found


def _download(model: SpeechModel, env: Mapping[str, str]) -> None:
    try:
        if model.engine == CONVERTED:
            ensure_converted(model, 1, env)   # downloads and converts in its own process, as a first load does
        elif model.engine == FASTER_WHISPER:
            from faster_whisper.utils import download_model
            download_model(model.repo, revision=model.revision)
        else:
            from huggingface_hub import snapshot_download
            snapshot_download(model.repo, revision=model.revision)
        logger.info("speech model %r downloaded", model.id)
    except Exception as e:  # noqa: BLE001 - a failed download is a log line; offering the model again retries
        logger.warning("speech model %r couldn't be downloaded: %s", model.id, type(e).__name__)
    finally:
        with _lock:
            _running.discard(model.id)


def start_download(model: SpeechModel, env: Mapping[str, str] = os.environ) -> None:
    """Fetch the model's files in the background, once at a time per model. Returns at once."""
    with _lock:
        if model.id in _running:
            return
        _running.add(model.id)
    thread = threading.Thread(target=_download, args=(model, env), name=f"speech-download-{model.id}", daemon=True)
    _threads.append(thread)
    thread.start()
