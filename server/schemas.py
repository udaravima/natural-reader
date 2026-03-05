"""
Pydantic request models for the TTS API.
"""
from pydantic import BaseModel


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_heart"  # Default voice (synced with frontend)
    speed: float = 1.0


class BatchTTSRequest(BaseModel):
    sentences: list[str]
    voice: str = "af_heart"
    speed: float = 1.0
