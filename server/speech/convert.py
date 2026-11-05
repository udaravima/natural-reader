"""A Transformers-format Whisper model turned into CTranslate2 format, once, the first time it's used.

Fine-tuned Whisper models are published in Transformers format (`model.safetensors`), which faster-whisper can't
load. The conversion takes seconds but needs `torch` and `transformers`, which only the Docling build has, and
which the backend otherwise never imports. So it runs in a process of its own (`python -m server.speech.convert`):
its memory goes back when it ends, and a crash there is a failed load here, not a dead worker.

The result lands in HF_HOME/speech-converted/<owner>--<name>--<revision>--int8. A conversion builds in a scratch
directory beside it and is renamed into place, so a half-finished one is never loaded. The original checkpoint
(about 1 GB for a `small`) is downloaded into that scratch directory too, and goes with it: once converted it is
never read again, so it isn't kept in the Hugging Face cache. One worker converts a model at a time (a lock file
per model in HF_HOME/speech-converting); another that wants it meanwhile waits, then loads the result.

inspect_repo answers an admin adding a model from the console: does it exist, is it Whisper, and does it need
converting. Nothing here imports a heavy package at module level: the admin routes import this.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ConversionFailed
from .languages import WHISPER_LANGUAGES

if TYPE_CHECKING:
    from .catalogue import SpeechModel

CONVERT_TIMEOUT_S = 3600   # the download is most of it; a large repo on a slow line can take a long while
LOCK_WAIT_S = CONVERT_TIMEOUT_S + 60   # a worker waiting on another's conversion waits as long as that may take
HUB_TIMEOUT_S = 10         # an admin is waiting on the lookup
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# What a conversion needs from a repo, and no more: repos also hold Flax weights, training state and sample audio.
_CONFIG_FILES = ["*.json", "*.txt"]
_SAFETENSORS = ["*.safetensors"]
_PICKLE_WEIGHTS = ["pytorch_model*.bin"]


class NotOnHub(Exception):
    """No such repo or revision, or a private or gated one this server has no token for."""


class NotWhisper(Exception):
    """The repo exists but isn't a Whisper model this app can run."""


class HubUnreachable(Exception):
    """Hugging Face couldn't be reached."""


@dataclass(frozen=True)
class RepoFacts:
    revision: str              # the commit, so the model can't change under the deployment
    engine: str                # "faster-whisper" or "converted-whisper"
    languages: tuple[str, ...]  # from the model card, when it names exactly one Whisper knows; else empty


def _hf_home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HF_HOME") or "~/.cache/huggingface").expanduser()


def converted_dir(model: SpeechModel, env: Mapping[str, str] = os.environ) -> Path:
    name = f"{model.repo.replace('/', '--')}--{model.revision or 'main'}--int8"
    return _hf_home(env) / "speech-converted" / name


def _lock_for(final: Path, env: Mapping[str, str] = os.environ):
    """The lock one model's conversion holds, shared by every worker on this server. It lives outside
    speech-converted, so nothing but models is in there."""
    from filelock import FileLock
    locks = _hf_home(env) / "speech-converting"
    locks.mkdir(parents=True, exist_ok=True)
    return FileLock(str(locks / f"{final.name}.lock"), timeout=LOCK_WAIT_S)


def ensure_converted(model: SpeechModel, cpu_threads: int, env: Mapping[str, str] = os.environ) -> Path:
    """The converted model's directory, converting it first if this server hasn't yet. Blocks: the engine calls
    it from its loading thread, and a second worker asking meanwhile waits for the first's result."""
    final = converted_dir(model, env)
    if (final / "model.bin").exists():
        return final
    from filelock import Timeout
    try:
        with _lock_for(final, env):
            if (final / "model.bin").exists():    # converted by another worker while this one waited
                return final
            return _convert_into(model, final, cpu_threads, env)
    except Timeout:
        raise ConversionFailed(f"Another worker has been converting {model.repo} for over an hour") from None


def _convert_into(model: SpeechModel, final: Path, cpu_threads: int, env: Mapping[str, str]) -> Path:
    final.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".converting-", dir=final.parent))
    try:
        built = scratch / "model"
        cmd = [sys.executable, "-m", "server.speech.convert", model.repo, model.revision or "main", str(built)]
        try:
            done = subprocess.run(cmd, cwd=_PROJECT_ROOT, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S,
                                  env={**env, "OMP_NUM_THREADS": str(cpu_threads), "TOKENIZERS_PARALLELISM": "false"})
        except subprocess.TimeoutExpired:
            raise ConversionFailed(f"Converting {model.repo} took too long and was stopped") from None
        except OSError as e:
            raise ConversionFailed(f"Converting {model.repo} couldn't start: {type(e).__name__}") from None
        if done.returncode < 0:   # stderr is a progress bar by then, and says nothing about why
            raise ConversionFailed(f"Converting {model.repo} was killed (signal {-done.returncode}): "
                                   "on a small server that's usually memory")
        if done.returncode != 0:
            lines = (done.stderr or "").strip().splitlines()
            raise ConversionFailed(f"Converting {model.repo} failed: {lines[-1][:200] if lines else 'no reason given'}")
        try:
            built.rename(final)
        except OSError:
            if not (final / "model.bin").exists():    # not a conversion from before the lock getting here first
                raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return final


def _api():
    from huggingface_hub import HfApi
    return HfApi()


def inspect_repo(repo: str, revision: str | None) -> RepoFacts:
    """Look a repo up on the Hub. Raises NotOnHub, NotWhisper or HubUnreachable."""
    from huggingface_hub.errors import (GatedRepoError, HFValidationError, RepositoryNotFoundError,
                                        RevisionNotFoundError)
    try:
        info = _api().model_info(repo, revision=revision or None, timeout=HUB_TIMEOUT_S)
    except (RepositoryNotFoundError, RevisionNotFoundError, GatedRepoError, HFValidationError) as e:
        raise NotOnHub(repo) from e
    except Exception as e:  # noqa: BLE001 - offline, a timeout, a 5xx: all "try again later"
        raise HubUnreachable(type(e).__name__) from e
    files = {s.rfilename for s in (info.siblings or [])}
    if "model.bin" in files and any(f == "tokenizer.json" or f.startswith("vocabulary.") for f in files):
        engine = "faster-whisper"
    elif any(f.endswith(".safetensors") or (f.startswith("pytorch_model") and f.endswith(".bin")) for f in files) \
            and (info.config or {}).get("model_type") == "whisper":
        engine = "converted-whisper"
    else:
        raise NotWhisper(repo)
    card = getattr(info, "card_data", None)
    language = getattr(card, "language", None) if card else None
    language = language[0] if isinstance(language, list) and len(language) == 1 else language
    # A card can say anything, and catalogue.parse refuses a language Whisper doesn't know: kept, the entry would
    # save and then be missing from the list.
    return RepoFacts(info.sha, engine, (language,) if isinstance(language, str) and language in WHISPER_LANGUAGES else ())


def _convert(repo: str, revision: str, out: str) -> None:
    """Runs in the separate process."""
    import ctranslate2
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer, WhisperFeatureExtractor

    # Into the scratch directory the parent removes afterwards, not the shared cache (module docstring).
    download = str(Path(out).parent / "download")
    source = snapshot_download(repo, revision=revision, allow_patterns=[*_CONFIG_FILES, *_SAFETENSORS],
                               cache_dir=download)
    if not any(Path(source).glob("*.safetensors")):   # older repos: Transformers loads these with weights_only
        source = snapshot_download(repo, revision=revision, allow_patterns=[*_CONFIG_FILES, *_PICKLE_WEIGHTS],
                                   cache_dir=download)
    ctranslate2.converters.TransformersConverter(source, low_cpu_mem_usage=True).convert(out, quantization="int8")
    # faster-whisper reads both of these beside the model. Many repos ship neither in the form it wants: without a
    # tokenizer.json it would fetch a stock one, and that doesn't match a fine-tune's added tokens.
    AutoTokenizer.from_pretrained(source).backend_tokenizer.save(os.path.join(out, "tokenizer.json"))
    WhisperFeatureExtractor.from_pretrained(source).to_json_file(os.path.join(out, "preprocessor_config.json"))


if __name__ == "__main__":
    try:
        _convert(*sys.argv[1:4])
    except Exception as e:  # noqa: BLE001 - the parent shows the last line to an admin
        print(f"{type(e).__name__}: {str(e).strip().splitlines()[0] if str(e).strip() else ''}", file=sys.stderr)
        sys.exit(1)
