"""What each stage of a simulated call costs (spec 2026-11-17-call-bench-round2): usage measured in the run times
the prices in prices.toml, each with its source, the date it was checked, and whether it is the provider's own.

- hearing: the audio sent, in minutes, rounded up to the billing increment;
- model: prompt and reply tokens as the provider reported them, per million;
- voice: characters (Chirp 3 HD), or text tokens (characters / 4, an estimate) plus 25 audio tokens a second of
  audio (Gemini-TTS).
A local engine costs nothing (it uses the server's CPU instead); an engine with no price is unknown (None), never
free."""
from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Any

PRICES = Path(__file__).resolve().parent / "prices.toml"


def load_prices(path: Path | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    raw = tomllib.loads((path or PRICES).read_text(encoding="utf-8"))
    checked = raw.get("checked")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for stage in ("hearing", "model", "voice"):
        out[stage] = {}
        for key, price in (raw.get(stage) or {}).items():
            out[stage][key] = {"checked": checked, "local": False, **price, "key": key}
    return out


def price_for(prices: dict, stage: str, spec: str) -> dict[str, Any] | None:
    """The price for an engine spec, else for shorter forms of it ("a:b:c", "a:b", "a")."""
    table = prices.get(stage) or {}
    parts = spec.split(":")
    for n in range(len(parts), 0, -1):
        found = table.get(":".join(parts[:n]))
        if found is not None:
            return found
    return None


def hearing_usd(price: dict | None, audio_s: float) -> float | None:
    if price is None:
        return None
    if price.get("local"):
        return 0.0
    step = price.get("increment_s")
    billed = math.ceil(audio_s / step - 1e-9) * step if step else audio_s
    return billed / 60 * price["per_minute"]


def model_usd(price: dict | None, prompt_tokens: int, reply_tokens: int) -> float | None:
    if price is None:
        return None
    if price.get("local"):
        return 0.0
    return (prompt_tokens * price["input_per_m"] + reply_tokens * price["output_per_m"]) / 1e6


def voice_usd(price: dict | None, chars: int, audio_s: float) -> float | None:
    if price is None:
        return None
    if price.get("local"):
        return 0.0
    if "per_m_chars" in price:
        return chars * price["per_m_chars"] / 1e6
    return (chars / 4 * price["text_per_m"] + audio_s * price["audio_tokens_per_s"] * price["audio_per_m"]) / 1e6


def per_1000_minutes(usd_per_call: float | None, call_minutes: float | None) -> float | None:
    """What 1,000 minutes of these calls cost: a call's cost spread over how long it lasts. A call's length counts
    every wait as well as speech, so a slower model makes this smaller while the cost of a call stays the same."""
    if usd_per_call is None or not call_minutes:
        return None
    return 1000 * usd_per_call / call_minutes


def model_shortfall(rows: list[dict], expected_turns: int) -> dict[str, Any]:
    """How many of a conversation's caller turns the model did not answer: the ones it errored on, and the ones it
    was never asked because nothing was heard (they have no row). The model's cost counts only the turns it
    answered, so it is a lower bound when any failed."""
    errors = [r["error"] for r in rows if r.get("error")]
    answered = len(rows) - len(errors)
    return {"turns": expected_turns, "failed": max(0, expected_turns - answered),
            "first_error": errors[0] if errors else None}
