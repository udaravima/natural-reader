from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import soundfile as sf
import base64
import io
import os
# import torch  # Kept for consistency if needed by other parts, but likely unused now
from kokoro_onnx import Kokoro
import uvicorn

# --- KOKORO SETUP ---
# Using ONNX model for CPU efficiency and to avoid CUDA warnings
MODEL_PATH = "kokoro-v1.0.onnx"
VOICES_PATH = "voices-v1.0.bin"

if not os.path.exists(MODEL_PATH) or not os.path.exists(VOICES_PATH):
    print("Error: Model files not found. Ensure 'kokoro-v1.0.onnx' and 'voices-v1.0.bin' are present. \nmodel_path: https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx,\nvoices_path: https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin")
    exit(1)

print(f"Loading Kokoro ONNX model from {MODEL_PATH}...")
kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
print("Kokoro loaded successfully.")

app = FastAPI()

# Allow CORS so our React frontend can talk to this server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TTSRequest(BaseModel):
    text: str
    voice: str = "bf_alice"  # Default voice
    speed: float = 1.0

class BatchTTSRequest(BaseModel):
    sentences: list[str]
    voice: str = "bf_alice"
    speed: float = 1.0

@app.post("/v1/synthesize")
async def synthesize(request: TTSRequest):
    """
    Accepts text, returns Base64 encoded WAV audio.
    """
    try:
        # Generate audio using ONNX model
        # lang='en-us' is equivalent to lang_code='a' in KPipeline
        audio, sample_rate = kokoro.create(
            request.text,
            voice=request.voice,
            speed=request.speed,
            lang="en-us"
        )
        
        if len(audio) == 0:
             raise HTTPException(status_code=400, detail="No audio generated")

        # Convert to standard WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format='WAV')
        buffer.seek(0)
        
        # Encode to base64 for easy transport to JSON
        b64_audio = base64.b64encode(buffer.read()).decode('utf-8')
        
        return {
            "audio_base64": b64_audio, 
            "duration_seconds": len(audio) / sample_rate
        }

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/batch_synthesize")
async def batch_synthesize(request: BatchTTSRequest):
    """
    Accepts a list of sentences, returns a single merged WAV audio as Base64.
    Useful for downloading entire pages as one audio file.
    """
    import numpy as np
    
    try:
        if not request.sentences:
            raise HTTPException(status_code=400, detail="No sentences provided")
        
        all_audio = []
        sample_rate = None
        
        # Generate audio for each sentence
        for sentence in request.sentences:
            if not sentence.strip():
                continue
            
            audio, sr = kokoro.create(
                sentence,
                voice=request.voice,
                speed=request.speed,
                lang="en-us"
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
    
    except Exception as e:
        print(f"Batch synthesis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("Starting Neural Voice Server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)