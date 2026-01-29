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
    print("Error: Model files not found. Ensure 'kokoro-v1.0.onnx' and 'voices-v1.0.bin' are present.")
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

if __name__ == "__main__":
    print("Starting Neural Voice Server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)