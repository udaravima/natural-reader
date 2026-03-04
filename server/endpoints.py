"""
TTS API endpoints with thread-safe inference via asyncio.Lock.

Bug fixes applied:
- Removed unreliable is_disconnected() checks that caused false
  "Client disconnected" spam (Starlette issue #1540)
- Added asyncio.Lock to serialize Kokoro inference (not thread-safe)
- Proper HTTPException re-raising so 400s don't become 500s
"""
import asyncio
import base64
import io

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException

from .model import kokoro
from .schemas import TTSRequest, BatchTTSRequest

router = APIRouter()

# Kokoro is not thread-safe — concurrent calls corrupt voice files and phonemizer.
# This lock serializes all inference calls.
inference_lock = asyncio.Lock()


@router.get("/v1/health")
async def health_check():
    """
    Lightweight health check — verifies the model is loaded without running
    inference or holding the inference lock. Safe to call during playback.
    """
    try:
        model_loaded = kokoro is not None
        return {
            "status": "ok" if model_loaded else "error",
            "model_loaded": model_loaded,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.post("/v1/synthesize")
async def synthesize(request: TTSRequest):
    """
    Accepts text, returns Base64 encoded WAV audio.
    """
    print(f"[TTS] Synthesize request: voice={request.voice}, speed={request.speed}")
    print(f"[TTS] Text: \"{request.text[:200]}{'...' if len(request.text) > 200 else ''}\"")
    try:
        async with inference_lock:
            loop = asyncio.get_event_loop()
            audio, sample_rate = await loop.run_in_executor(
                None,
                lambda: kokoro.create(
                    request.text,
                    voice=request.voice,
                    speed=request.speed,
                    lang="en-us"
                )
            )

        if len(audio) == 0:
            raise HTTPException(status_code=400, detail="No audio generated")

        # Convert to standard WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format='WAV')
        buffer.seek(0)

        # Encode to base64 for easy transport to JSON
        b64_audio = base64.b64encode(buffer.read()).decode('utf-8')
        duration = len(audio) / sample_rate
        print(f"[TTS] ✓ Synthesized {duration:.1f}s audio")

        return {
            "audio_base64": b64_audio,
            "duration_seconds": duration
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[TTS] ✗ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/batch_synthesize")
async def batch_synthesize(request: BatchTTSRequest):
    """
    Accepts a list of sentences, returns a single merged WAV audio as Base64.
    Holds the lock for the entire batch to avoid per-sentence queue contention.
    """
    print(f"[TTS] Batch request: {len(request.sentences)} sentences, voice={request.voice}, speed={request.speed}")
    try:
        if not request.sentences:
            raise HTTPException(status_code=400, detail="No sentences provided")

        all_audio = []
        sample_rate = None
        loop = asyncio.get_event_loop()

        async with inference_lock:
            for i, sentence in enumerate(request.sentences):
                if not sentence.strip():
                    continue

                audio, sr = await loop.run_in_executor(
                    None,
                    lambda s=sentence: kokoro.create(
                        s,
                        voice=request.voice,
                        speed=request.speed,
                        lang="en-us"
                    )
                )

                if sample_rate is None:
                    sample_rate = sr

                if len(audio) > 0:
                    all_audio.append(audio)
                    # Add a short silence (0.3 seconds) between sentences
                    silence = np.zeros(int(sample_rate * 0.3), dtype=audio.dtype)
                    all_audio.append(silence)

        if not all_audio:
            raise HTTPException(status_code=400, detail="No audio generated from sentences")

        # Concatenate all audio segments
        merged_audio = np.concatenate(all_audio)

        # Convert to WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, merged_audio, sample_rate, format='WAV')
        buffer.seek(0)

        b64_audio = base64.b64encode(buffer.read()).decode('utf-8')

        return {
            "audio_base64": b64_audio,
            "duration_seconds": len(merged_audio) / sample_rate,
            "sentence_count": len(request.sentences)
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Batch synthesis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
