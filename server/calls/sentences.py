"""Talk's sentence cuts, in Python (src/lib/speech/sentences.js), so the benchmark's "first piece ready" is the
moment Talk would start voicing it: a piece ends at a full stop, question or exclamation mark followed by white
space, or at a blank line; while nothing has been said yet, past `first_clause_chars` characters (Talk's
SPEECH_WINDOW_FIRST_CLAUSE_CHARS, 30 by default; 0 never) the opening is cut at a comma, semicolon or colon instead.
Markdown is dropped and a piece with no letter or digit is not spoken. Talk's markdown handling is richer; the
replies a call agent writes are plain sentences, so emphasis, code marks, headings and list marks are enough here."""
from __future__ import annotations

import re

_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n\s*\n")
_CLAUSE = re.compile(r"[,;:]\s+")
_SAYS_SOMETHING = re.compile(r"[^\W_]")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKS = re.compile(r"[*_`]+")
_LINE_START = re.compile(r"(?m)^\s*(?:#{1,6}\s+|[-+*]\s+|\d+[.)]\s+|>\s*)")


def plain(raw: str) -> str:
    text = _LINK.sub(r"\1", raw)
    text = _LINE_START.sub("", text)
    text = _MARKS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceSplitter:
    def __init__(self, first_clause_chars: int = 30) -> None:
        self._buffer = ""
        self._first_clause = first_clause_chars
        self._opening = first_clause_chars > 0

    def _emit(self, raw: str, out: list[str]) -> None:
        text = plain(raw)
        if _SAYS_SOMETHING.search(text):
            self._opening = False
            out.append(text)

    def _next_cut(self) -> tuple[int, int] | None:
        sentence = _BOUNDARY.search(self._buffer)
        end = (sentence.start(), sentence.end()) if sentence else None
        if not self._opening:
            return end
        clause = _CLAUSE.search(self._buffer, self._first_clause)
        if not clause or (end and end[0] <= clause.start()):
            return end
        return clause.start() + 1, clause.end()

    def feed(self, delta: str) -> list[str]:
        """The pieces this delta completed, in order."""
        self._buffer += delta
        out: list[str] = []
        cut = self._next_cut()
        while cut:
            self._emit(self._buffer[:cut[0]], out)
            self._buffer = self._buffer[cut[1]:]
            cut = self._next_cut()
        return out

    def flush(self) -> list[str]:
        out: list[str] = []
        raw = self._buffer.rstrip()
        if raw:
            self._emit(raw, out)
        self._buffer = ""
        return out
