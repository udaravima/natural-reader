"""Phone config from env vars: off by default, a public listener needs an allow list, bad numbers fall back."""
import logging

from server.phone.config import PhoneConfig, is_loopback_host, load_phone_config, normalize_ip


def test_defaults_are_off():
    cfg = load_phone_config({})
    assert cfg == PhoneConfig(listen=None, allow=(), pbx_address=None, max_calls=2, idle_s=30)


def test_public_listen_without_allow_is_off(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": "0.0.0.0:9092"})
    assert cfg.listen is None
    assert "PHONE_AUDIOSOCKET_ALLOW" in caplog.text


def test_public_listen_with_allow_is_on():
    cfg = load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": "0.0.0.0:9092",
                             "PHONE_AUDIOSOCKET_ALLOW": "192.0.2.1, 192.0.2.2 ,,"})
    assert cfg.listen == "0.0.0.0:9092"
    assert cfg.allow == ("192.0.2.1", "192.0.2.2")


def test_loopback_listen_needs_no_allow():
    for listen in ("127.0.0.1:9092", "[::1]:9092", "localhost:9092"):
        assert load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": listen}).listen == listen


def test_bad_listen_is_off(caplog):
    for bad in ("9092", "127.0.0.1", "127.0.0.1:abc", "127.0.0.1:70000", ":9092"):
        with caplog.at_level(logging.WARNING):
            assert load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": bad}).listen is None


def test_bad_allow_entries_are_skipped_and_mapped_ones_normalised(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": "0.0.0.0:9092",
                                 "PHONE_AUDIOSOCKET_ALLOW": "not-an-ip,::ffff:192.0.2.9"})
    assert cfg.allow == ("192.0.2.9",)
    assert "not-an-ip" in caplog.text


def test_only_bad_allow_entries_still_means_no_allow_list():
    cfg = load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": "0.0.0.0:9092", "PHONE_AUDIOSOCKET_ALLOW": "nope"})
    assert cfg.listen is None


def test_pbx_address_rules():
    assert load_phone_config({"PHONE_AUDIOSOCKET_LISTEN": "127.0.0.1:9092"}).pbx_address == "127.0.0.1:9092"
    wildcard = {"PHONE_AUDIOSOCKET_LISTEN": "0.0.0.0:9092", "PHONE_AUDIOSOCKET_ALLOW": "192.0.2.1"}
    assert load_phone_config(wildcard).pbx_address is None
    assert load_phone_config({**wildcard, "PHONE_AUDIOSOCKET_LISTEN": "[::]:9092"}).pbx_address is None
    assert load_phone_config({**wildcard, "PHONE_PBX_ADDRESS": " pbx.example.com:9092 "}).pbx_address == \
        "pbx.example.com:9092"


def test_numbers_and_bad_numbers(caplog):
    cfg = load_phone_config({"PHONE_MAX_CALLS": "5", "PHONE_IDLE_S": "12.5"})
    assert (cfg.max_calls, cfg.idle_s) == (5, 12.5)
    with caplog.at_level(logging.WARNING):
        bad = load_phone_config({"PHONE_MAX_CALLS": "many", "PHONE_IDLE_S": "-1"})
    assert (bad.max_calls, bad.idle_s) == (2, 30)
    assert "PHONE_MAX_CALLS" in caplog.text


def test_max_utterance_s(caplog):
    """R5.8: the longest stretch of caller sound heard as one clip; 30 s unless set, 5 to 120."""
    assert load_phone_config({}).max_utterance_s == 30
    assert load_phone_config({"PHONE_MAX_UTTERANCE_S": "12.5"}).max_utterance_s == 12.5
    for bad in ("4", "121", "-1", "forever", "nan"):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert load_phone_config({"PHONE_MAX_UTTERANCE_S": bad}).max_utterance_s == 30
        assert "PHONE_MAX_UTTERANCE_S" in caplog.text, bad


def test_model_keep_alive(caplog):
    """R17.1: how long a line's model stays loaded after a load or a turn, in whole seconds, read like the app's other
    keep-alive durations (inference_limits.seconds): 15 min unless set, 0 is off, a negative value is "forever"."""
    assert load_phone_config({}).model_keep_alive_s == 900
    for raw, seconds in (("15m", 900), ("1h", 3600), ("600", 600), ("90s", 90), ("2.5m", 150), ("0", 0),
                         ("-1", -1), ("-5m", -1)):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert load_phone_config({"PHONE_MODEL_KEEP_ALIVE": raw}).model_keep_alive_s == seconds, raw
        assert caplog.text == "", raw
    for bad in ("forever", "10 minutes", "1d", "nan"):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert load_phone_config({"PHONE_MODEL_KEEP_ALIVE": bad}).model_keep_alive_s == 900
        assert caplog.text.count("PHONE_MODEL_KEEP_ALIVE") == 1, bad


def test_address_helpers():
    assert normalize_ip("::ffff:192.0.2.9") == "192.0.2.9"
    assert normalize_ip("2001:db8::1") == "2001:db8::1"
    assert normalize_ip("junk") is None
    assert is_loopback_host("127.0.0.2") and is_loopback_host("::1") and is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0") and not is_loopback_host("192.0.2.1") and not is_loopback_host("pbx")
