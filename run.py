"""
Neural Voice Server — Entry Point

Starts the Kokoro TTS server on http://localhost:8000.

Set `WORKERS=N` to spawn multiple uvicorn workers. Each worker loads its own
Kokoro model into RAM, so this is a memory/throughput trade-off — useful when
exporting audiobooks (the audiobook loop pipelines up to 3 page-synths in
parallel; with a single worker they queue, with N workers they actually fan
out across cores). Defaults to 1 to keep idle RAM low.
"""
import os
import uvicorn

if __name__ == "__main__":
    workers = max(1, int(os.environ.get("WORKERS", "1")))
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting Neural Voice Server on http://{host}:{port} (workers={workers})")
    # When workers > 1 uvicorn needs an import string so each child can
    # re-import the app. The single-worker path is unchanged.
    if workers > 1:
        uvicorn.run("server.app:app", host=host, port=port, workers=workers)
    else:
        from server.app import app
        uvicorn.run(app, host=host, port=port)