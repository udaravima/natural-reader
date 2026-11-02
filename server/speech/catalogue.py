"""The speech models an admin can choose between, and which one applies (spec 2026-11-02-speech-model-choice).

Three sources, a later one replacing an earlier entry with the same id: the list below, the deployer's YAML file
(SPEECH_MODELS_FILE, read once per process), and the models an admin added in the console (stored in the
chat_options row). A bad entry is a warning and is left out: it can never stop the server or hide the others.

An entry's `engine` says how it runs:
  faster-whisper         already CTranslate2: a Whisper size, a repo, or (file and environment only) a directory
  converted-whisper      a Transformers-format repo, converted once on first use (speech/convert.py)
  transformers-whisper   the same kind of repo run as it is through Transformers: slower, and useful to compare

Which model applies, first match wins: the admin's choice, SPEECH_MODEL (a catalogue id, or anything faster-whisper
takes), then `small`. Only the `local` speech engine uses any of this: a remote one is named by SPEECH_MODEL alone.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from .convert import converted_dir
from .languages import WHISPER_LANGUAGES

logger = logging.getLogger("server.speech")

DEFAULT_MODEL = "small"
FASTER_WHISPER, CONVERTED, TRANSFORMERS = "faster-whisper", "converted-whisper", "transformers-whisper"
ENGINES = (FASTER_WHISPER, CONVERTED, TRANSFORMERS)
FILE_VAR = "SPEECH_MODELS_FILE"
NEEDS_TORCH = "Needs the Python packages transformers and torch, which the Docling build of the server has."
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*\Z")
REVISION_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,99}\Z")


@dataclass(frozen=True)
class SpeechModel:
    id: str
    label: str
    engine: str
    repo: str
    revision: str | None = None
    languages: tuple[str, ...] = ()    # empty: every language Whisper knows
    license: str = ""
    commercial: str = ""               # "yes", "no" or "check", as the list's author judged it
    note: str = ""
    source: str = "built-in"           # "built-in" | "file" | "admin" | "environment"


def _size(name: str) -> SpeechModel:
    return SpeechModel(name, f"Whisper {name}", FASTER_WHISPER, name)


_BUILT_IN = (
    *(_size(name) for name in ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")),
    SpeechModel("whisper-turbo", "Whisper large-v3-turbo, pinned", FASTER_WHISPER,
                "deepdml/faster-whisper-large-v3-turbo-ct2", "4df90f75321148c3a29a9e2351b7ddf8f5b115a8", (), "mit", "yes",
                "Good for English. Weak on Tamil, and unusable on Sinhala."),
    SpeechModel("whisper-small-si", "Whisper small, Sinhala (run12)", CONVERTED,
                "SinhaSpeech/whisper-small-sinhala-run12-best", "96eac6280bc937432db4b58c5ec2641826732552", ("si",),
                "apache-2.0", "check", "Sinhala only: English comes out as gibberish. Its training data includes "
                "audio of unknown licence."),
    SpeechModel("whisper-small-si-run15", "Whisper small, Sinhala (run15)", CONVERTED,
                "Yohan2003/whisper-small-sinhala-run15-backup", "ab6154e9f312ce3f8803e624a84942213f523af3", ("si",),
                "apache-2.0", "check", "Trained with some English, so by its model card English stays readable."),
    SpeechModel("whisper-small-si-hf", "Whisper small, Sinhala (run12, unconverted)", TRANSFORMERS,
                "SinhaSpeech/whisper-small-sinhala-run12-best", "96eac6280bc937432db4b58c5ec2641826732552", ("si",),
                "apache-2.0", "check", "The run12 weights through Transformers: about 4.6 times slower and 3 times "
                "the memory. Useful to compare."),
    SpeechModel("whisper-small-ta", "Whisper small, Tamil", CONVERTED, "vasista22/whisper-tamil-small",
                "ac0d71cd70d4c66aec7126996cd9e56e97e698b7", ("ta",), "apache-2.0", "yes",
                "Not yet tried on Sri Lankan Tamil: test it before trusting it."),
)

_from_file: tuple[SpeechModel, ...] | None = None
_warned: set[str] = set()


def reset_for_tests() -> None:
    global _from_file
    _from_file = None
    _warned.clear()


def _warn_once(message: str) -> None:
    """The list is read on every speech request, so a standing problem is said once per process."""
    if message not in _warned:
        _warned.add(message)
        logger.warning(message)


def parse(model_id: object, raw: object, source: str) -> SpeechModel:
    """One entry, or ValueError saying what's wrong with it. A local directory is for a deployer: an entry in the
    database always names a repo."""
    if not isinstance(model_id, str) or not ID_RE.match(model_id):
        raise ValueError("the id must be lower-case letters, digits, dots, dashes or underscores")
    if not isinstance(raw, Mapping):
        raise ValueError("it isn't a mapping")
    engine, repo = raw.get("engine"), raw.get("repo")
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("it has no repo")
    repo = repo.strip()
    if (engine != FASTER_WHISPER or source == "admin") and not REPO_RE.match(repo):
        raise ValueError("it needs a Hugging Face repo as owner/name")
    revision = raw.get("revision")
    if revision is not None and not (isinstance(revision, str) and REVISION_RE.match(revision)):
        raise ValueError("the revision isn't a commit or a branch name")
    languages = raw.get("languages") or []
    if not isinstance(languages, list) or not all(isinstance(c, str) and c in WHISPER_LANGUAGES for c in languages):
        raise ValueError("languages must be a list of Whisper language codes")
    return SpeechModel(model_id, str(raw.get("label") or model_id), engine, repo, revision, tuple(languages),
                       str(raw.get("license") or ""), str(raw.get("commercial") or ""),
                       " ".join(str(raw.get("note") or "").split()), source)


def _entries(raw: Mapping, source: str) -> list[SpeechModel]:
    models = []
    for model_id, entry in raw.items():
        try:
            models.append(parse(model_id, entry, source))
        except ValueError as e:
            _warn_once(f"Speech model {model_id!r} left out: {e}")
    return models


def _read_file(path: str) -> tuple[SpeechModel, ...]:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:  # noqa: BLE001 - a missing file, bad YAML: this must never stop the server
        logger.warning("%s=%s couldn't be read: %s", FILE_VAR, path, type(e).__name__)
        return ()
    if data is None:
        return ()
    if isinstance(data, Mapping) and isinstance(data.get("models"), Mapping):
        data = data["models"]
    if not isinstance(data, Mapping):
        logger.warning("%s=%s isn't a mapping of models", FILE_VAR, path)
        return ()
    return tuple(_entries(data, "file"))


def all_models(env: Mapping[str, str] = os.environ, added: Mapping | None = None) -> dict[str, SpeechModel]:
    """Everything an admin can choose, by id, in display order."""
    global _from_file
    path = (env.get(FILE_VAR) or "").strip()
    if path and _from_file is None:
        _from_file = _read_file(path)
    models = {m.id: m for m in _BUILT_IN}
    models.update({m.id: m for m in (_from_file or ()) if path})
    models.update({m.id: m for m in _entries(added or {}, "admin")})
    return models


def wanted(options, env: Mapping[str, str] = os.environ) -> SpeechModel:
    """The model that applies under these chat options."""
    models = all_models(env, options.added_speech_models)
    choice = options.speech_model
    if choice in models:
        return models[choice]
    if choice:
        _warn_once(f"The chosen speech model {choice!r} is no longer in the list: using the server setting")
    value = (env.get("SPEECH_MODEL") or "").strip() or DEFAULT_MODEL
    return models.get(value) or SpeechModel(value, value, FASTER_WHISPER, value, source="environment")


def source(options, env: Mapping[str, str] = os.environ) -> str:
    """Which of the three decided: "admin" | "env" | "default"."""
    if options.speech_model in all_models(env, options.added_speech_models):
        return "admin"
    return "env" if (env.get("SPEECH_MODEL") or "").strip() else "default"


def _installed(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def why_not(model: SpeechModel, env: Mapping[str, str] = os.environ) -> str:
    """Why this server can't run the model, or empty. A model already converted needs nothing more."""
    if model.engine == FASTER_WHISPER:
        return ""
    if model.engine == CONVERTED and (converted_dir(model, env) / "model.bin").exists():
        return ""
    return "" if _installed("torch") and _installed("transformers") else NEEDS_TORCH


def view(model: SpeechModel, env: Mapping[str, str] = os.environ) -> dict:
    """What the console shows for one model."""
    return {"id": model.id, "label": model.label, "engine": model.engine, "repo": model.repo,
            "revision": model.revision, "languages": list(model.languages),
            "language_names": [WHISPER_LANGUAGES[code] for code in model.languages],
            "license": model.license, "commercial": model.commercial, "note": model.note,
            "removable": model.source == "admin", "why_not": why_not(model, env)}
