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

from server.logging_config import configure_logging

if __name__ == "__main__":
    # Configure logging before anything else so startup messages are captured to
    # the rotating log file too. `log_config=None` below stops uvicorn from
    # replacing this config with its own console-only setup.
    logfile = configure_logging()
    workers = max(1, int(os.environ.get("WORKERS", "1")))
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting Neural Voice Server on http://{host}:{port} (workers={workers}); logging to {logfile}")
    # When workers > 1 uvicorn needs an import string so each child can
    # re-import the app. The single-worker path is unchanged.
    if workers > 1:
        uvicorn.run("server.app:app", host=host, port=port, workers=workers, log_config=None)
    else:
        from server.app import app
        uvicorn.run(app, host=host, port=port, log_config=None)