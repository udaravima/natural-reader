from server.auth.config import load_auth_config, dev_bypass_allowed


def test_defaults_enabled():
    cfg = load_auth_config({})
    assert cfg.enabled is True
    assert cfg.session_ttl_hours == 168
    assert cfg.cookie_secure is True


def test_disabled_flag_parsed():
    cfg = load_auth_config({"AUTH_ENABLED": "false"})
    assert cfg.enabled is False


def test_dev_bypass_only_on_localhost():
    cfg = load_auth_config({"AUTH_ENABLED": "false"})
    assert dev_bypass_allowed(cfg, "127.0.0.1") is True
    assert dev_bypass_allowed(cfg, "0.0.0.0") is False


def test_dev_bypass_false_when_auth_enabled():
    cfg = load_auth_config({})
    assert dev_bypass_allowed(cfg, "127.0.0.1") is False


def test_oidc_values_parsed():
    cfg = load_auth_config(
        {"OIDC_ISSUER": "https://kc/realms/nr", "OIDC_CLIENT_ID": "app"}
    )
    assert cfg.oidc_issuer == "https://kc/realms/nr"
    assert cfg.oidc_client_id == "app"
