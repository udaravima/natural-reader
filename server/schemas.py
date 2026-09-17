"""
Pydantic request models for the TTS API.

Size caps (SEC-4): Kokoro inference runs under a single global lock, so one
oversized request stalls TTS for every client. These bounds keep a single
call — or a single batch — from monopolizing the model. All three are
env-tunable for deployments with different throughput/latency trade-offs.
"""
import os

from pydantic import BaseModel, Field
from typing_extensions import Annotated

# Max characters in a single /v1/synthesize request. ~20k chars ≈ several
# paragraphs / a long page — well above any real read-aloud selection.
MAX_TEXT_CHARS = int(os.environ.get("TTS_MAX_TEXT_CHARS", "20000"))
# Max sentences in a /v1/batch_synthesize request (audiobook/page batches).
MAX_SENTENCES = int(os.environ.get("TTS_MAX_SENTENCES", "2000"))
# Max characters per sentence in a batch.
MAX_SENTENCE_CHARS = int(os.environ.get("TTS_MAX_SENTENCE_CHARS", "5000"))


class TTSRequest(BaseModel):
    text: str = Field(max_length=MAX_TEXT_CHARS)
    voice: str = "af_heart"  # Default voice (synced with frontend)
    speed: float = 1.0


class BatchTTSRequest(BaseModel):
    sentences: list[Annotated[str, Field(max_length=MAX_SENTENCE_CHARS)]] = Field(
        max_length=MAX_SENTENCES
    )
    voice: str = "af_heart"
    speed: float = 1.0
