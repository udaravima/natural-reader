import json

from server.services.deployment_info import readonly_rows, safe_url


def _by_id(env):
    return {r["id"]: r for r in readonly_rows(env)}


def test_secret_values_never_appear():
    env = {"INFERENCE_PROVIDERS": "cloud", "INFERENCE_CLOUD_KIND": "openai",
           "INFERENCE_CLOUD_URL": "https://user:pw@llm.example.com:8443/v1?key=q",
           "INFERENCE_CLOUD_API_KEY": "sk-top", "SESSION_SECRET": "s" * 40}
    rows = readonly_rows(env)
    dumped = json.dumps(rows)
    assert "sk-top" not in dumped and "s" * 40 not in dumped and "pw" not in dumped and "key=q" not in dumped
    by_id = {r["id"]: r for r in rows}
    assert by_id["provider:cloud"]["value"] == "openai · https://llm.example.com:8443/v1"
    assert by_id["provider_key:cloud"]["value"] == "set"
    assert by_id["session_secret"]["value"] == "set" and by_id["kc_admin_client_secret"]["value"] == "not set"


def test_safe_url_drops_user_info_query_and_fragment():
    assert safe_url("http://a:b@h.example.com:1/p?x=1#f") == "http://h.example.com:1/p"


def test_sources_name_the_variable_or_the_default():
    rows = _by_id({})
    assert rows["reader_model"]["value"] == "The chat model of each conversation"
    assert rows["reader_model"]["source"] == "default" and rows["reader_model"]["env"] is None
    row = _by_id({"SUMMARIZE_MODEL": "m"})["reader_model"]
    assert (row["value"], row["env"], row["source"]) == ("m", "SUMMARIZE_MODEL", "env")
    assert _by_id({"READER_MODEL": "chat"})["reader_model"]["value"] == "The chat model of each conversation"


def test_defaults_and_env_values_come_from_the_given_env():
    d = _by_id({})
    assert d["provider:ollama"]["value"] == "ollama · http://localhost:11434"
    assert (d["allowed_models"]["value"], d["chat_timeout"]["value"], d["daily_budget"]["value"]) == (
        "All models allowed", "300 s", "Unlimited")
    assert (d["embedding_model"]["value"], d["docling"]["value"], d["workers"]["value"]) == (
        "nomic-embed-text", "Off", "1")
    assert d["searxng"]["value"] == "localhost:18043" and d["searxng"]["source"] == "default"
    assert not any(r["source"] == "env" for r in d.values())
    e = _by_id({"DOCLING_ENABLED": "TRUE", "SEARXNG_URL": "", "PDF_STORAGE_DIR": "/srv/pdfs", "WORKERS": "3",
                "INFERENCE_DAILY_TOKEN_BUDGET": "5000"})
    assert e["docling"]["value"] == "On" and e["docling"]["source"] == "env"
    assert e["searxng"]["value"] == "Not set up"
    assert (e["storage"]["value"], e["storage"]["env"]) == ("/srv/pdfs", "PDF_STORAGE_DIR")
    assert e["workers"]["value"] == "3" and e["daily_budget"]["value"] == "5000"


def test_a_malformed_url_shows_a_placeholder_instead_of_failing():
    env = {"INFERENCE_PROVIDERS": "x", "INFERENCE_X_KIND": "openai", "INFERENCE_X_URL": "http://[::1",
           "SEARXNG_URL": "http://[bad", "OLLAMA_URL": "http://[nope"}
    rows = _by_id(env)
    assert rows["provider:x"]["value"] == "Not a valid URL" and rows["provider:x"]["source"] == "env"
    assert rows["provider:x"]["env"] == "INFERENCE_X_URL"
    assert rows["searxng"]["value"] == "Not a valid URL" and rows["searxng"]["source"] == "env"
    assert rows["embedding_url"]["value"] == "Not a valid URL"
