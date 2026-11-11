"""
Pydantic request models for the TTS API.

Size caps (SEC-4): Kokoro inference runs under a single global lock, so one
oversized request stalls TTS for every client. These bounds keep a single
call — or a single batch — from monopolizing the model. All four are
env-tunable for deployments with different throughput/latency trade-offs.
"""
import os

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Annotated

# Max characters in a single /v1/synthesize request. ~20k chars ≈ several
# paragraphs / a long page — well above any real read-aloud selection.
MAX_TEXT_CHARS = int(os.environ.get("TTS_MAX_TEXT_CHARS", "20000"))
# Max sentences in a /v1/batch_synthesize request (audiobook/page batches).
MAX_SENTENCES = int(os.environ.get("TTS_MAX_SENTENCES", "2000"))
# Max characters per sentence in a batch.
MAX_SENTENCE_CHARS = int(os.environ.get("TTS_MAX_SENTENCE_CHARS", "5000"))
# Max characters in a whole batch. The two limits above multiply to 10 million characters, hours of synthesis
# for one request; 200,000 is a very long page or reply (a few hours of audio is never one request: the
# audiobook export sends a page at a time).
MAX_BATCH_CHARS = int(os.environ.get("TTS_MAX_BATCH_CHARS", "200000"))


class TTSRequest(BaseModel):
    text: str = Field(max_length=MAX_TEXT_CHARS)
    voice: str = "af_heart"  # Default voice (synced with frontend)
    speed: float = 1.0


class ChatVoiceRequest(TTSRequest):
    """A piece of a reply to read aloud (POST /v1/chat/voice). `google_voice`: the person's own Google voice, used
    when Google reads the replies and it is one of Google's (spec 2026-11-04-speech-choices); else the admin's."""
    google_voice: str | None = Field(default=None, max_length=40)


class BatchTTSRequest(BaseModel):
    sentences: list[Annotated[str, Field(max_length=MAX_SENTENCE_CHARS)]] = Field(
        max_length=MAX_SENTENCES
    )
    voice: str = "af_heart"
    speed: float = 1.0

    @model_validator(mode="after")
    def _total_within_the_cap(self) -> "BatchTTSRequest":
        total = sum(len(s) for s in self.sentences)
        if total > MAX_BATCH_CHARS:
            raise ValueError(f"The batch is {total:,} characters; at most {MAX_BATCH_CHARS:,} per request.")
        return self
