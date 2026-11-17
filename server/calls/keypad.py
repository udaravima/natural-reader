"""Keying digits on the phone's keypad instead of saying them (spec 2026-11-17-call-bench-round2).

With `--keypad` the agent gets a `collect_digits` tool. Calling it plays the scenario's recorded instruction ("key
in your phone number, then press the hash key"), the scripted caller keys the call's digits, and the tool returns
them, so a phone number or a booking reference can't be misheard. On a phone line these would be DTMF tones; a
browser call would show a keypad. Keying is taken to start 1.0 s after the instruction and to take 0.4 s a key,
the closing hash included: an assumption, not a measurement, settable on the command line."""
from __future__ import annotations

import numpy as np

from .simtools import SimTool

KEYPAD_RULES = """The caller can key numbers on their phone's keypad.
- To get a phone number or a booking reference, call collect_digits straight away, without asking for it first: a
  recorded line asks the caller to key it in. Never ask the caller to say a number.
- Booking references are keyed as their digits only, such as 10421 for LB-10421."""

_ROWS = {**dict.fromkeys("123A", 697), **dict.fromkeys("456B", 770), **dict.fromkeys("789C", 852),
         **dict.fromkeys("*0#D", 941)}
_COLUMNS = {**dict.fromkeys("147*", 1209), **dict.fromkeys("2580", 1336), **dict.fromkeys("369#", 1477),
            **dict.fromkeys("ABCD", 1633)}


def keying_s(digits: str, start_s: float = 1.0, per_key_s: float = 0.4) -> float:
    """How long the caller takes to key `digits` and the closing hash, from the end of the instruction."""
    return start_s + per_key_s * (len(digits) + 1)


def dtmf(digits: str, rate: int, *, start_s: float = 1.0, per_key_s: float = 0.4, tone_s: float = 0.12
         ) -> np.ndarray:
    """The keying as it would sound: silence, then each key's two tones and the closing hash, `keying_s` long."""
    out = np.zeros(int(round(keying_s(digits, start_s, per_key_s) * rate)), dtype=np.float32)
    n = int(round(tone_s * rate))
    t = np.arange(n) / rate
    fade = np.minimum(1.0, np.minimum(np.arange(n), np.arange(n)[::-1]) / max(1, int(0.005 * rate)))
    for k, key in enumerate(f"{digits}#"):
        tone = 0.25 * (np.sin(2 * np.pi * _ROWS[key] * t) + np.sin(2 * np.pi * _COLUMNS[key] * t)) * fade
        at = int(round((start_s + k * per_key_s) * rate))
        out[at:at + n] = tone[:len(out) - at]
    return out


def keypad_tool(keys: dict[str, str]) -> SimTool:
    """`collect_digits` for one call: it returns what the scripted caller keys for `what`."""
    def collect_digits(state: dict, args: dict) -> dict:
        what = args["what"]
        if what not in keys:
            return {"what": what, "digits": "",
                    "note": "The caller pressed the hash key without keying anything. Ask how else you can help."}
        return {"what": what, "digits": keys[what]}

    return SimTool("collect_digits",
                   "Ask the caller to key a phone number or a booking reference on their phone's keypad and press "
                   "hash. A recorded line asks them; you get the digits they keyed. Use it instead of asking them "
                   "to say a number.",
                   {"type": "object", "properties": {"what": {"type": "string", "enum": ["phone", "reference"]}},
                    "required": ["what"]},
                   collect_digits)
