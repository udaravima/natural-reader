"""What /v1/health answers. Apart from endpoints.py so tests can read it without loading the Kokoro model."""
from __future__ import annotations


def health_payload(*, model_loaded: bool, db_ready: bool) -> dict:
    """`status` is read-aloud's own answer (the reader's Kokoro switch reads it), so it stays "ok" with the
    database down; `database` says whether sign-in, chat and documents can work."""
    return {"status": "ok" if model_loaded else "error", "model_loaded": model_loaded,
            "database": "ok" if db_ready else "unavailable"}
