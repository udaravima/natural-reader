import httpx
import respx
from server.services import embeddings


@respx.mock
async def test_embed_one_uses_model_router(monkeypatch):
    # model_router re-reads env on every call — setenv steers both the
    # upstream URL and the model name.
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("EMBEDDING_MODEL", "my-embed")
    route = respx.post("http://ollama.test/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0] * 768})
    )
    await embeddings.start_client()
    try:
        await embeddings.embed_one("some text")
    finally:
        await embeddings.stop_client()
    body = route.calls.last.request.content.decode()
    assert '"model": "my-embed"' in body or '"model":"my-embed"' in body


@respx.mock
async def test_embed_one_env_change_takes_effect_without_reload(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    route = respx.post("http://ollama.test/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0] * 768})
    )
    await embeddings.start_client()
    try:
        monkeypatch.setenv("EMBEDDING_MODEL", "first-model")
        await embeddings.embed_one("text one")
        monkeypatch.setenv("EMBEDDING_MODEL", "second-model")
        await embeddings.embed_one("text two")
    finally:
        await embeddings.stop_client()
    bodies = [call.request.content.decode() for call in route.calls]
    assert "first-model" in bodies[0]
    assert "second-model" in bodies[1]
