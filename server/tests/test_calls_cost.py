"""Round 2 of the call benchmark: what each stage of a call costs, from usage and a price list that names its
sources (spec 2026-11-17-call-bench-round2)."""
import pytest

from server.calls.cost import (hearing_usd, load_prices, model_shortfall, model_usd, per_1000_minutes, price_for,
                              voice_usd)


@pytest.fixture(scope="module")
def prices():
    return load_prices()


def test_local_engines_cost_nothing(prices):
    for stage, spec in (("hearing", "whisper:small.en"), ("voice", "kokoro:af_heart")):
        price = price_for(prices, stage, spec)
        assert price["local"] is True
    assert hearing_usd(price_for(prices, "hearing", "whisper:small.en"), 600) == 0.0
    assert voice_usd(price_for(prices, "voice", "kokoro:af_heart"), 5000, 300) == 0.0


def test_a_minute_on_google_costs_its_per_minute_price_and_part_seconds_round_up(prices):
    google = price_for(prices, "hearing", "google:projects/p/locations/asia-southeast1/recognizers/r")
    assert google["per_minute"] == 0.016
    assert hearing_usd(google, 60) == pytest.approx(0.016)
    assert hearing_usd(google, 59.2) == pytest.approx(0.016)   # billed by the second: 60 s
    assert price_for(prices, "hearing", "google-stream:projects/p/locations/us/recognizers/_@chirp_3")


def test_the_model_costs_its_tokens(prices):
    lite = price_for(prices, "model", "gemini:gemini-3.1-flash-lite")
    assert model_usd(lite, 1_000_000, 1_000_000) == pytest.approx(0.25 + 1.50)


def test_chirp_voices_cost_by_the_character_and_gemini_tts_by_tokens(prices):
    chirp = price_for(prices, "voice", "chirp:en-IN-Chirp3-HD-Kore")
    assert voice_usd(chirp, 1000, 60) == pytest.approx(0.03)
    lite = price_for(prices, "voice", "google:gemini-2.5-flash-lite-preview-tts:Kore")
    assert voice_usd(lite, 400, 10) == pytest.approx((100 * 0.5 + 250 * 10.0) / 1e6)


def test_an_engine_without_a_price_is_unknown_not_free(prices):
    assert price_for(prices, "hearing", "openai:whisper-small@http://example.com") is None
    assert price_for(prices, "model", "ollama:llama3") is None


def test_every_paid_price_names_its_source_date_and_whether_it_is_the_providers_own(prices):
    for stage in ("hearing", "model", "voice"):
        for key, price in prices[stage].items():
            if price.get("local"):
                continue
            assert price["source"].startswith("https://"), key
            assert price["checked"] == "2026-11-17" and isinstance(price["official"], bool), key


def test_per_1000_minutes_spreads_a_calls_cost_over_how_long_it_lasts():
    assert per_1000_minutes(0.02, 1.25) == pytest.approx(16.0)   # 2 cents for a 1.25-minute call
    assert per_1000_minutes(None, 1.25) is None   # an unpriced call stays unknown
    assert per_1000_minutes(0.02, None) is None
    assert per_1000_minutes(0.02, 0) is None


def test_a_turn_the_model_errored_on_or_was_never_asked_leaves_the_model_cost_short():
    rows = [{"error": None}, {"error": "HTTP 429: busy"}, {"error": ""}, {"error": "HTTP 503: high demand"}]
    # 5 caller turns: 2 answered, 2 errored, 1 never asked (nothing was heard, so no row)
    assert model_shortfall(rows, 5) == {"turns": 5, "failed": 3, "first_error": "HTTP 429: busy"}
    assert model_shortfall(rows[:1], 1) == {"turns": 1, "failed": 0, "first_error": None}


def test_a_local_ollama_model_costs_nothing_but_a_cloud_one_is_not_assumed_free(prices):
    assert price_for(prices, "model", "ollama:gemma4:e4b")["local"] is True
    assert price_for(prices, "model", "ollama:gemma4:31b-cloud") is None   # Ollama's cloud bills: unknown, not free
