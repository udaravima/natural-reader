"""Scoring a simulated call (spec 2026-11-17-call-simulation).

Each check maps to something to fix or fine-tune:
- `tools` / `never_tools`: the model's tool discipline (it must look things up, and must not book on a complaint);
- `says`: the caller heard what matters (the price, the reference);
- `money`: an amount the agent said that no tool returned (an invented price), the worst failure on a sales line;
- `booking` / `cancelled` / `transferred`: the call achieved its purpose, so a misheard name or phone number shows
  up here, beside the hearing's word error rate;
- `answered`: no turn ended in silence after a tool ran.

Words are compared after `normalize`: lower case, apostrophes dropped and other punctuation as spaces, commas inside
numbers dropped, the words zero to nine read as digits and runs of numbers joined ("zero seven seven, one two three"
is "077123", so a phone number read out digit by digit matches 077-123), ordinals as "12th" ("twenty-fourth" is
"24th"), letters said one by one joined ("L B" is "lb"), and letters run into digits split ("lb10421" is
"lb 10421"). What's left is a real difference in what was heard, not in how it was written."""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations_with_replacement
from typing import Any

from .scenario import CallScript
from .simtools import ToolRun

_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9"}
_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")
_NUMBER = re.compile(r"\d+")
_CURRENCY = r"(?:rs\.?|lkr|rupees?)"
_MONEY = re.compile(rf"\b\d{{1,3}}(?:,\d{{3}})+\b|\b{_CURRENCY}\s*(\d[\d,]*)|(\d[\d,]*)\s*{_CURRENCY}(?![a-z])",
                    re.IGNORECASE)


_UNIT_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
                  "eighth": 8, "ninth": 9}
_ORDINALS = {**_UNIT_ORDINALS, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
             "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
             "twentieth": 20, "thirtieth": 30}
_TENS = {"twenty": 20, "thirty": 30}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def normalize(text: str) -> str:
    t = _THOUSANDS_COMMA.sub("", (text or "").lower())
    t = re.sub(r"['\u2019]", "", t)               # that's is thats, so its s can't join a spelled name
    t = re.sub(r"[^\w\s]|_", " ", t)
    t = re.sub(r"(?<=[a-z])(?=\d)", " ", t)       # lb10421 is lb 10421
    out: list[str] = []
    for w in t.split():
        if w in _UNIT_ORDINALS and out and out[-1] in _TENS:   # twenty fourth is 24th
            out[-1] = _ordinal(_TENS[out[-1]] + _UNIT_ORDINALS[w])
            continue
        if w in _ORDINALS:
            out.append(_ordinal(_ORDINALS[w]))
            continue
        w = _DIGIT_WORDS.get(w, w)
        if w.isdigit() and out and out[-1].isdigit():            # a number read digit by digit is one number
            out[-1] += w
            continue
        out.append(w)
    return " ".join(_join_letters(out))


def _join_letters(words: list[str]) -> list[str]:
    """Letters said one by one ("L B", "T H I L I N I") are one word."""
    out: list[str] = []
    run: list[str] = []
    for w in words + [""]:
        if len(w) == 1 and w.isalpha():
            run.append(w)
            continue
        if run:
            out.append("".join(run))
            run = []
        if w:
            out.append(w)
    return out


def wer(reference: str, heard: str) -> float:
    """Word error rate of `heard` against `reference`, after normalize: (substitutions + insertions + deletions)
    / words in the reference. 0 is perfect; above 1 is possible when much was added."""
    ref, hyp = normalize(reference).split(), normalize(heard).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / len(ref)


def money(text: str) -> set[int]:
    """Amounts of money in a reply: numbers written with thousands commas, or next to rs, LKR or rupees. Phone
    numbers and booking references have neither, so they are left out."""
    found = set()
    for m in _MONEY.finditer(text or ""):
        raw = m.group(1) or m.group(2) or m.group(0)
        digits = raw.replace(",", "")
        if digits.isdigit():
            found.add(int(digits))
    return found


def _numbers(value: Any, into: set[int]) -> set[int]:
    if isinstance(value, bool):
        return into
    if isinstance(value, (int, float)):
        into.add(int(value))
    elif isinstance(value, str):
        into.update(int(n) for n in _NUMBER.findall(_THOUSANDS_COMMA.sub("", value)))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, into)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers(v, into)
    return into


def _allowed(runs: list[ToolRun]) -> set[int]:
    """Every number a tool returned, the sum of any two, and two to four times one: "two rooms come to"."""
    base = set()
    for run in runs:
        _numbers(run.result, base)
    big = sorted(n for n in base if n >= 100)
    derived = {a + b for a, b in combinations_with_replacement(big, 2)}
    derived |= {n * k for n in big for k in (2, 3, 4)}
    return base | derived


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class CallScore:
    passed: bool
    checks: tuple[Check, ...]


def _matches(field: str, wanted: Any, got: Any) -> bool:
    if field == "guest_name":
        return set(normalize(str(wanted)).split()) <= set(normalize(str(got or "")).split())
    if field == "phone":
        return re.sub(r"\D", "", str(wanted)) == re.sub(r"\D", "", str(got or ""))
    return str(wanted).strip().lower() == str(got).strip().lower()


def _booking_check(wanted: dict[str, Any], state: dict[str, Any], original: dict[str, Any]) -> Check:
    before = {b.get("reference") for b in original.get("bookings") or ()}
    made = [b for b in state.get("bookings") or () if b.get("reference") not in before]
    if not made:
        return Check("booking", False, "no booking was made")
    best, misses = None, None
    for b in made:
        wrong = [f"{k}: wanted {v!r}, got {b.get(k)!r}" for k, v in wanted.items() if not _matches(k, v, b.get(k))]
        if misses is None or len(wrong) < len(misses):
            best, misses = b, wrong
    if not misses:
        return Check("booking", True, f"{best.get('reference')} as asked")
    return Check("booking", False, f"{best.get('reference')}: " + "; ".join(misses))


def score_call(script: CallScript, replies: list[str], runs: list[ToolRun], state: dict[str, Any],
               original: dict[str, Any], *, unanswered: int = 0) -> CallScore:
    """`replies`: what the agent said, turn by turn; `runs`: every tool run in the call; `state`: the data at the
    end; `original`: the scenario's data before the call; `unanswered`: turns that ended in silence."""
    e = script.expect
    checks: list[Check] = []
    ok_names = {r.name for r in runs if r.ok}
    ran_names = {r.name for r in runs}
    if e.tools:
        missing = [t for t in e.tools if t not in ok_names]
        detail = "; ".join(f"{t} {'ran only with errors' if t in ran_names else 'never ran'}" for t in missing)
        checks.append(Check("tools", not missing, detail or "all ran"))
    if e.never_tools:
        bad = [t for t in e.never_tools if t in ran_names]
        checks.append(Check("never_tools", not bad, f"{', '.join(bad)} ran" if bad else "none ran"))
    said = f" {normalize(' '.join(replies))} "
    if e.says:
        missing_groups = [g for g in e.says if not any(f" {normalize(p)} " in said for p in g)]
        checks.append(Check("says", not missing_groups,
                            "; ".join("never said " + " / ".join(g) for g in missing_groups) or "all said"))
    allowed = _allowed(runs)
    invented = sorted({n for reply in replies for n in money(reply)} - allowed)
    checks.append(Check("money", not invented,
                        f"said {', '.join(f'{n:,}' for n in invented)}, which no tool returned" if invented
                        else "every amount came from a tool"))
    if e.booking is not None:
        checks.append(_booking_check(e.booking, state, original))
    if e.cancelled:
        statuses = {b.get("reference"): b.get("status") for b in state.get("bookings") or ()}
        left = [ref for ref in e.cancelled if statuses.get(ref) != "cancelled"]
        checks.append(Check("cancelled", not left, f"{', '.join(left)} still {statuses.get(left[0], 'missing')}"
                            if left else "cancelled"))
    if e.transferred is not None:
        did = bool(state.get("transferred"))
        checks.append(Check("transferred", did == e.transferred,
                            ("transferred" if did else "not transferred") + (", as expected" if did == e.transferred
                                                                            else ", unexpectedly")))
    checks.append(Check("answered", unanswered == 0,
                        f"{unanswered} turn{'s' if unanswered != 1 else ''} ended in silence after a tool"
                        if unanswered else "every turn answered"))
    return CallScore(all(c.ok for c in checks), tuple(checks))
