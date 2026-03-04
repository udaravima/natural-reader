"""
Pydantic request models for the TTS API.
"""
from pydantic import BaseModel


class TTSRequest(BaseModel):
    text: str
    voice: str = "bf_alice"  # Default voice
    speed: float = 1.0


class BatchTTSRequest(BaseModel):
    sentences: list[str]
    voice: str = "bf_alice"
    speed: float = 1.0
