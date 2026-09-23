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
    assert dev_bypass_allowed(cfg, "localhost") is True
    assert dev_bypass_allowed(cfg, "::1") is True
    assert dev_bypass_allowed(cfg, "0.0.0.0") is False
    # An IPv4-mapped spoof and a Host-header-style value must not qualify.
    assert dev_bypass_allowed(cfg, "::ffff:127.0.0.1") is False
    assert dev_bypass_allowed(cfg, "evil.example.com") is False


def test_dev_bypass_false_when_auth_enabled():
    cfg = load_auth_config({})
    assert dev_bypass_allowed(cfg, "127.0.0.1") is False


def test_oidc_values_parsed():
    cfg = load_auth_config(
        {"OIDC_ISSUER": "https://kc/realms/nr", "OIDC_CLIENT_ID": "app"}
    )
    assert cfg.oidc_issuer == "https://kc/realms/nr"
    assert cfg.oidc_client_id == "app"


def test_kc_admin_fields_and_availability():
    from server.auth.config import kc_admin_available

    cfg = load_auth_config({
        "KC_ADMIN_CLIENT_ID": "natural-reader-admin",
        "KC_ADMIN_CLIENT_SECRET": "s3cr3t",
    })
    assert cfg.kc_admin_client_id == "natural-reader-admin"
    assert cfg.kc_admin_client_secret == "s3cr3t"
    assert kc_admin_available(cfg) is True


def test_kc_admin_unavailable_when_unset():
    from server.auth.config import kc_admin_available

    assert kc_admin_available(load_auth_config({})) is False
    assert kc_admin_available(load_auth_config({"KC_ADMIN_CLIENT_ID": "x"})) is False
