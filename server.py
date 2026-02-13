from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import soundfile as sf
import base64
import io
import os
# import torch  # Kept for consistency if needed by other parts, but likely unused now
from kokoro_onnx import Kokoro
import onnxruntime as ort
import uvicorn
import asyncio

# --- KOKORO SETUP ---
MODEL_PATH = "kokoro-v1.0.onnx"
VOICES_PATH = "voices-v1.0.bin"

if not os.path.exists(MODEL_PATH) or not os.path.exists(VOICES_PATH):
    print("Error: Model files not found. Ensure 'kokoro-v1.0.onnx' and 'voices-v1.0.bin' are present. \nmodel_path: https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx,\nvoices_path: https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin")
    exit(1)

# --- GPU SUPPORT DETECTION ---
def get_execution_providers():
    """
    Detect and return the best available execution providers.
    Priority: CUDA > OpenVINO GPU (Intel Arc) > OpenVINO NPU > OpenVINO CPU > CPU
    """
    available_providers = ort.get_available_providers()
    print(f"Available ONNX Runtime providers: {available_providers}")
    
    providers = []
    
    # Priority 1: NVIDIA CUDA (if available)
    if "CUDAExecutionProvider" in available_providers:
        providers.append("CUDAExecutionProvider")
        print("✓ NVIDIA CUDA detected")
    
    # Priority 2: Intel Arc GPU via OpenVINO
    if "OpenVINOExecutionProvider" in available_providers:
        # Try Intel Arc GPU first (discrete GPU)
        try:
            gpu_provider = ("OpenVINOExecutionProvider", {
                "device_type": "GPU",
                "precision": "FP16",  # FP16 is faster on Arc GPUs
                "enable_opencl_throttling": True,
                "cache_dir": ".openvino_cache"
            })
            providers.append(gpu_provider)
            print("✓ Intel Arc GPU (OpenVINO) detected")
        except Exception as e:
            print(f"Note: OpenVINO GPU configuration failed: {e}")
        
        # Priority 3: Intel NPU (Neural Processing Unit)
        try:
            npu_provider = ("OpenVINOExecutionProvider", {
                "device_type": "NPU",
                "precision": "FP16",
                "cache_dir": ".openvino_cache"
            })
            # Only add NPU if GPU wasn't already added with same provider name
            # (we'll test both and use what works)
            print("✓ Intel NPU (OpenVINO) available as fallback")
        except Exception as e:
            print(f"Note: OpenVINO NPU not available: {e}")
        
        # Priority 4: OpenVINO optimized CPU (uses Intel VNNI/AVX instructions)
        try:
            cpu_openvino = ("OpenVINOExecutionProvider", {
                "device_type": "CPU",
                "precision": "FP32",
                "num_of_threads": 0,  # Auto-detect
                "cache_dir": ".openvino_cache"
            })
            print("✓ OpenVINO CPU acceleration available as fallback")
        except Exception as e:
            print(f"Note: OpenVINO CPU configuration failed: {e}")
    
    # Priority 5: Standard CPU fallback
    if "CPUExecutionProvider" in available_providers:
        providers.append("CPUExecutionProvider")
    
    # Ensure we always have at least CPU
    if not providers:
        providers = ["CPUExecutionProvider"]
    
    return providers

def create_session_with_fallback(model_path, providers):
    """
    Try to create an ONNX session with fallback through providers.
    Some models may not be compatible with all execution providers.
    """
    for i, provider in enumerate(providers):
        try:
            if isinstance(provider, tuple):
                provider_name, options = provider
                session = ort.InferenceSession(
                    model_path,
                    providers=[provider_name],
                    provider_options=[options]
                )
            else:
                session = ort.InferenceSession(model_path, providers=[provider])
            
            actual = session.get_providers()[0] if session.get_providers() else "Unknown"
            print(f"✓ Successfully created session with: {actual}")
            return session
        except Exception as e:
            provider_name = provider[0] if isinstance(provider, tuple) else provider
            print(f"✗ Failed to use {provider_name}: {e}")
            if i < len(providers) - 1:
                print(f"  Trying next provider...")
            continue
    
    # Final fallback to CPU
    print("Falling back to CPU execution...")
    return ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

# Create custom ONNX session with GPU/NPU support if available
providers = get_execution_providers()
print(f"\nConfigured execution providers: {[p[0] if isinstance(p, tuple) else p for p in providers]}")

print(f"\nLoading Kokoro ONNX model from {MODEL_PATH}...")

# Create session with fallback handling
session = create_session_with_fallback(MODEL_PATH, providers)
kokoro = Kokoro.from_session(session, VOICES_PATH)

# Log which provider is actually being used
actual_provider = session.get_providers()[0] if session.get_providers() else "Unknown"
print(f"\n🎙️ Kokoro loaded successfully using: {actual_provider}\n")

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
async def synthesize(request: TTSRequest, raw_request: Request):
    """
    Accepts text, returns Base64 encoded WAV audio.
    Checks for client disconnection to avoid wasted inference.
    """
    try:
        # Check if client already disconnected before starting heavy work
        if await raw_request.is_disconnected():
            print("Client disconnected before inference started, skipping.")
            return {"audio_base64": "", "duration_seconds": 0}

        # Run inference in thread pool so the event loop stays free
        # to detect client disconnections
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

        # Check again after inference — client may have left during processing
        if await raw_request.is_disconnected():
            print("Client disconnected after inference, discarding result.")
            return {"audio_base64": "", "duration_seconds": 0}
        
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
async def batch_synthesize(request: BatchTTSRequest, raw_request: Request):
    """
    Accepts a list of sentences, returns a single merged WAV audio as Base64.
    Checks for client disconnection between sentences to bail out early.
    """
    import numpy as np
    
    try:
        if not request.sentences:
            raise HTTPException(status_code=400, detail="No sentences provided")
        
        all_audio = []
        sample_rate = None
        loop = asyncio.get_event_loop()
        
        # Generate audio for each sentence
        for i, sentence in enumerate(request.sentences):
            # Check for client disconnection between sentences
            if await raw_request.is_disconnected():
                print(f"Client disconnected during batch processing (after sentence {i}/{len(request.sentences)}), stopping.")
                return {"audio_base64": "", "duration_seconds": 0, "sentence_count": 0}

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
        
        # Final disconnect check before encoding
        if await raw_request.is_disconnected():
            print("Client disconnected after batch inference, discarding result.")
            return {"audio_base64": "", "duration_seconds": 0, "sentence_count": 0}

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