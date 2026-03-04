"""
Neural Voice Server — Entry Point

Starts the Kokoro TTS server on http://localhost:8000
"""
import uvicorn
from server.app import app

if __name__ == "__main__":
    print("Starting Neural Voice Server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)