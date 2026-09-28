"""
Security-hardening regression tests (sub-project A).

Each test pins a specific finding from the May 2026 audit so a regression
re-opens as a red test rather than a silent vuln:

  SEC-1  doc_id path traversal   — hex validation on path params, plus
                                    storage-path containment. (Upload doc ids
                                    are the server's own hash, never a body
                                    field.)
  SEC-3  CORS wildcard           — origins are env-driven, never a bare "*".
  SEC-4  TTS request size caps   — bound text / sentence payloads (DoS).

These are deliberately DB- and model-free: request validation runs before any
handler body, so we assert on the validation layer (Pydantic + FastAPI 422).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


# --------------------------------------------------------------------------
# SEC-1 — doc_id path traversal
# --------------------------------------------------------------------------

def _docs_app() -> FastAPI:
    # Router only — no create_app(), so no startup DB init and no model load.
    # The doc routes now require auth, and FastAPI resolves that dependency
    # before validating the doc_id path pattern — so we stub an authenticated
    # principal here to reach (and assert) the hex validation itself.
    from server.auth import deps
    from server.routers import docs

    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id="00000000-0000-0000-0000-000000000001", email="a@x.io", role="admin",
        capabilities=frozenset({"reader"}),
    )
    app.include_router(docs.router)
    return app


def test_path_route_rejects_non_hex_doc_id():
    # A non-hex doc_id on a path parameter must be rejected by validation (422)
    # before the handler ever runs. Without validation this route reaches the
    # DB-guard and returns 503, so 422 proves the pattern is enforced.
    client = TestClient(_docs_app())
    resp = client.get("/v1/docs/not-a-valid-hex-doc-id")
    assert resp.status_code == 422


def test_storage_place_rejects_escape(monkeypatch, tmp_path):
    from server.services import doc_storage

    # Even if a bad doc_id ever reached the storage helper, it must never
    # resolve to a path outside DOC_STORAGE_DIR — and must refuse before
    # moving anything.
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path / "store"))
    staged_file = tmp_path / "staged.part"
    staged_file.write_bytes(b"%PDF-1.4")
    staged = doc_storage.Staged(staged_file, "a" * 64, 8, "pdf")
    with pytest.raises(ValueError):
        doc_storage.place(staged, "../../../../tmp/escape")
    assert staged_file.exists()


# --------------------------------------------------------------------------
# SEC-3 — CORS origins are env-driven, not a wildcard
# --------------------------------------------------------------------------

def test_cors_origins_parsed_and_stripped():
    from server.appconfig import parse_cors_origins

    assert parse_cors_origins("https://a.com, https://b.com ") == [
        "https://a.com",
        "https://b.com",
    ]


def test_cors_origins_blank_falls_back_to_wildcard_default():
    # Default stays permissive so the content-script extension (arbitrary page
    # origins) keeps working until it's decoupled via the background worker.
    from server.appconfig import parse_cors_origins

    assert parse_cors_origins(None) == ["*"]
    assert parse_cors_origins("   ") == ["*"]


def test_cors_credentials_disabled_for_wildcard():
    # Wildcard + credentials is invalid per the CORS spec and the SEC-3 footgun.
    from server.appconfig import cors_allow_credentials

    assert cors_allow_credentials(["*"]) is False


def test_cors_credentials_enabled_for_explicit_origins():
    # Cookies (sub-project B) are only allowed once origins are pinned.
    from server.appconfig import cors_allow_credentials

    assert cors_allow_credentials(["https://app.example.com"]) is True


# --------------------------------------------------------------------------
# SEC-4 — TTS request size caps
# --------------------------------------------------------------------------

def test_tts_request_rejects_oversized_text():
    from server.schemas import TTSRequest

    with pytest.raises(ValidationError):
        TTSRequest(text="x" * 100_000)


def test_tts_request_accepts_normal_text():
    from server.schemas import TTSRequest

    m = TTSRequest(text="Hello world.")
    assert m.text == "Hello world."


def test_batch_request_rejects_too_many_sentences():
    from server.schemas import BatchTTSRequest

    with pytest.raises(ValidationError):
        BatchTTSRequest(sentences=["hi"] * 100_000)


def test_batch_request_rejects_oversized_sentence():
    from server.schemas import BatchTTSRequest

    with pytest.raises(ValidationError):
        BatchTTSRequest(sentences=["x" * 100_000])


def test_batch_request_accepts_normal_sentences():
    from server.schemas import BatchTTSRequest

    m = BatchTTSRequest(sentences=["First sentence.", "Second one."])
    assert len(m.sentences) == 2
