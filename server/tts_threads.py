"""How many threads Kokoro gets. Every core is not the fastest choice: on 24 cores it took 583 ms with all of them
and 525 ms with 6, because the work stops scaling and the threads get in each other's way. The rest stay free for
Whisper and a local model."""
import logging
import os
from collections.abc import Mapping

logger = logging.getLogger(__name__)

DEFAULT_THREADS = 6


def tts_threads(env: Mapping[str, str] = os.environ, cores: int | None = None) -> int:
    default = min(DEFAULT_THREADS, cores or os.cpu_count() or 1)
    raw = (env.get("TTS_CPU_THREADS") or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        logger.warning("TTS_CPU_THREADS=%r is not a number; using %d", raw, default)
        return default
