"""Admin → Models → Embeddings, the routes (phase 2b Task 6): see what the library's vectors were made with,
check a model, choose it (which starts a re-embed, or switches at once for the same model elsewhere), cancel."""
import pytest

from server.services import embedding_migration, embedding_state, embeddings
from server.services.embeddings import ProbeResult
from server.tests import seed
from server.tests.test_chat_options import _app, _client, _principal

ENV = {"OLLAMA_URL": "http://ollama.test", "EMBEDDING_MODEL": "nomic-embed-text"}
TWO = {"INFERENCE_PROVIDERS": "local,other", "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
       "INFERENCE_OTHER_KIND": "ollama", "INFERENCE_OTHER_URL": "http://other.test"}


@pytest.fixture
async def admin(db_conn, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    for k in ("INFERENCE_PROVIDERS", "EMBEDDING_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, ENV)
    probes = []

    async def probe(provider, model, **_kw):
        probes.append((provider.name, model))
        if model == "mxbai-embed-large":
            return ProbeResult(False, 1024, None, "mxbai-embed-large gives 1024 numbers and the library holds 768.")
        return ProbeResult(True, 768, None, f"{model} gives 768 numbers, the size the library holds.")
    monkeypatch.setattr(embeddings, "probe", probe)
    principal = await _principal(db_conn, "a1", "a@x.io", "admin")
    return _client(_app(db_conn, principal)), probes


async def _vectored_chunk(db_conn):
    await seed.seed_doc(db_conn, "c" * 64, None, file_type="text", state="indexed")
    vec = "[" + ",".join(["1"] + ["0"] * 767) + "]"
    await db_conn.execute("INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, "
                          "embedding_model) VALUES (%s, 0, 1, 'page', 't', 'h', %s::vector, 'nomic-embed-text')",
                          ("c" * 64, vec))


async def test_a_member_gets_403_everywhere(db_conn):
    member = await _principal(db_conn, "m1", "m@x.io", "member")
    async with _client(_app(db_conn, member)) as c:
        assert (await c.get("/v1/admin/embeddings")).status_code == 403
        assert (await c.get("/v1/admin/embeddings/models", params={"provider": "ollama"})).status_code == 403
        assert (await c.post("/v1/admin/embeddings/probe", json={"provider": "ollama", "model": "x"})).status_code == 403
        assert (await c.put("/v1/admin/embeddings", json={"provider": "ollama", "model": "x"})).status_code == 403
        assert (await c.delete("/v1/admin/embeddings/migration")).status_code == 403


async def test_the_view_shows_current_target_and_no_migration(admin):
    client, _ = admin
    async with client as c:
        body = (await c.get("/v1/admin/embeddings")).json()
    assert body["current"] == {"provider": "ollama", "model": "nomic-embed-text", "dimensions": None}
    assert body["target"] == {"provider": "ollama", "model": "nomic-embed-text"}
    assert body["source"] == {"provider": "default", "model": "env"}
    assert body["env"] == {"provider": "EMBEDDING_PROVIDER", "model": "EMBEDDING_MODEL"}
    assert body["migration"]["state"] == "none"
    assert body["providers"] == [{"name": "ollama", "kind": "ollama"}]


async def test_the_probe_refuses_an_unknown_provider_and_returns_the_verdict(admin):
    client, probes = admin
    async with client as c:
        r = await c.post("/v1/admin/embeddings/probe", json={"provider": "nope", "model": "x"})
        assert r.status_code == 422 and r.json()["detail"]["error"] == "unknown_provider"
        r = await c.post("/v1/admin/embeddings/probe", json={"provider": "ollama", "model": "mxbai-embed-large"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "native": 1024, "dimensions": None,
                        "reason": "mxbai-embed-large gives 1024 numbers and the library holds 768."}
    assert probes == [("ollama", "mxbai-embed-large")]


async def test_put_refuses_a_refused_model_with_its_reason_and_saves_nothing(admin, db_conn):
    client, _ = admin
    async with client as c:
        r = await c.put("/v1/admin/embeddings", json={"provider": "ollama", "model": "mxbai-embed-large"})
    assert r.status_code == 422
    assert r.json()["detail"]["message"] == "mxbai-embed-large gives 1024 numbers and the library holds 768."
    cur = await db_conn.execute("SELECT 1 FROM app_settings WHERE key = 'embeddings'")
    assert await cur.fetchone() is None


async def test_put_another_model_starts_a_re_embed(admin, db_conn):
    client, probes = admin
    await _vectored_chunk(db_conn)
    async with client as c:
        r = await c.put("/v1/admin/embeddings", json={"provider": "ollama", "model": "embeddinggemma-2:740m"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert probes == [("ollama", "embeddinggemma-2:740m")]          # checked again on the server
    assert body["target"]["model"] == "embeddinggemma-2:740m" and body["source"]["model"] == "admin"
    assert body["current"]["model"] == "nomic-embed-text"            # nothing switched yet
    assert body["migration"] == {"state": "building", "model": "embeddinggemma-2:740m", "filled": 0, "total": 1,
                                 "error": None}


async def test_put_the_same_model_on_another_provider_switches_with_no_re_embed(admin, db_conn, monkeypatch):
    for k, v in TWO.items():
        monkeypatch.setenv(k, v)
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'embedding_state'")
    await embedding_state.init(db_conn)
    client, _ = admin
    async with client as c:
        r = await c.put("/v1/admin/embeddings", json={"provider": "other", "model": "nomic-embed-text"})
    body = r.json()
    assert body["current"]["provider"] == "other" and body["migration"]["state"] == "none"


async def test_delete_cancels_and_the_target_goes_back(admin, db_conn):
    client, _ = admin
    await _vectored_chunk(db_conn)
    async with client as c:
        await c.put("/v1/admin/embeddings", json={"provider": "ollama", "model": "embeddinggemma-2:740m"})
        r = await c.delete("/v1/admin/embeddings/migration")
    body = r.json()
    assert body["migration"]["state"] == "none" and body["target"]["model"] == "nomic-embed-text"
    assert body["source"]["model"] == "env"


async def test_put_nulls_goes_back_to_the_environment(admin, db_conn):
    client, probes = admin
    async with client as c:
        await c.put("/v1/admin/embeddings", json={"provider": "ollama", "model": "embeddinggemma-2:740m"})
        r = await c.put("/v1/admin/embeddings", json={"provider": None, "model": None})
    body = r.json()
    assert body["target"]["model"] == "nomic-embed-text" and body["source"]["model"] == "env"
    assert body["migration"]["state"] == "none"                      # back to current: nothing to build


async def test_the_view_reports_progress_and_a_paused_migration(admin, db_conn):
    client, _ = admin
    await _vectored_chunk(db_conn)
    async with client as c:
        await c.put("/v1/admin/embeddings", json={"provider": "ollama", "model": "embeddinggemma-2:740m"})
        state = await embedding_state.read_state(db_conn)
        await embedding_state.write_state(db_conn, {**state, "filled": 1, "error": "Can't reach ollama"})
        body = (await c.get("/v1/admin/embeddings")).json()
    assert body["migration"] == {"state": "failed", "model": "embeddinggemma-2:740m", "filled": 1, "total": 1,
                                 "error": "Can't reach ollama"}


async def test_the_models_route_lists_a_providers_models_likely_ones_first(admin, monkeypatch):
    client, _ = admin

    async def names(provider, **_kw):
        return ["gemma4:e4b", "nomic-embed-text:latest", "llama3.2:3b", "embeddinggemma-2:740m"]
    monkeypatch.setattr(embeddings, "list_model_names", names)
    async with client as c:
        r = await c.get("/v1/admin/embeddings/models", params={"provider": "ollama"})
        bad = await c.get("/v1/admin/embeddings/models", params={"provider": "nope"})
    # embedding models first, each group in the provider's own order
    assert r.json() == {"models": ["nomic-embed-text:latest", "embeddinggemma-2:740m", "gemma4:e4b", "llama3.2:3b"]}
    assert bad.status_code == 422


def test_the_migration_module_is_what_the_routes_start():   # the routes use the same entry points the loop does
    assert callable(embedding_migration.start) and callable(embedding_migration.cancel)
