"""
Kokoro ONNX model loading with GPU/NPU/CPU auto-detection and fallback.
"""
import os
import onnxruntime as ort
from kokoro_onnx import Kokoro

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
            providers.append(npu_provider)
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
            providers.append(cpu_openvino)
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


# --- Initialize model on module import ---
providers = get_execution_providers()
print(f"\nConfigured execution providers: {[p[0] if isinstance(p, tuple) else p for p in providers]}")

print(f"\nLoading Kokoro ONNX model from {MODEL_PATH}...")

# Create session with fallback handling
session = create_session_with_fallback(MODEL_PATH, providers)
kokoro = Kokoro.from_session(session, VOICES_PATH)

# Log which provider is actually being used
actual_provider = session.get_providers()[0] if session.get_providers() else "Unknown"
print(f"\n🎙️ Kokoro loaded successfully using: {actual_provider}\n")
