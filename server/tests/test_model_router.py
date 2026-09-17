from server.services.model_router import load_inference_config, is_model_allowed


def test_defaults():
    cfg = load_inference_config({})
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.timeout_s == 300
    assert cfg.allowed_models is None          # unset = allow everything
    assert cfg.summarize_model == "llama3.2:3b"
    assert cfg.embed_model == "nomic-embed-text"
    assert cfg.daily_token_budget is None      # unset = unlimited


def test_allowlist_parses_comma_separated_with_whitespace():
    cfg = load_inference_config({"INFERENCE_MODELS": "gemma3, qwen2.5 ,, deepseek-r1:1.5b"})
    assert cfg.allowed_models == ("gemma3", "qwen2.5", "deepseek-r1:1.5b")
    assert is_model_allowed(cfg, "gemma3")
    assert not is_model_allowed(cfg, "llama3.2:3b")


def test_empty_allowlist_means_all():
    cfg = load_inference_config({"INFERENCE_MODELS": "  "})
    assert cfg.allowed_models is None
    assert is_model_allowed(cfg, "anything")


def test_summarize_model_precedence():
    assert load_inference_config({}).summarize_model == "llama3.2:3b"
    assert load_inference_config(
        {"WEB_SEARCH_SUMMARY_MODEL": "old"}).summarize_model == "old"
    assert load_inference_config({
        "WEB_SEARCH_SUMMARY_MODEL": "old",
        "SUMMARIZE_MODEL": "new",
    }).summarize_model == "new"


def test_budget_zero_and_unset_mean_unlimited():
    assert load_inference_config({}).daily_token_budget is None
    assert load_inference_config(
        {"INFERENCE_DAILY_TOKEN_BUDGET": "0"}).daily_token_budget is None
    assert load_inference_config(
        {"INFERENCE_DAILY_TOKEN_BUDGET": "500000"}).daily_token_budget == 500000


def test_ollama_url_trailing_slash_stripped():
    cfg = load_inference_config({"OLLAMA_URL": "http://ollama.test/"})
    assert cfg.ollama_url == "http://ollama.test"
