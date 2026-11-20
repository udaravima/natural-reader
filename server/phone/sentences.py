"""Talk's sentence cuts, in Python (src/lib/speech/sentences.js), so a phone call voices a reply at the same points
Talk does. A piece ends at a full stop, question or exclamation mark followed by white space, or at a blank line;
while nothing has been said yet, past `first_clause_chars` characters (0: never) the opening is cut at a comma,
semicolon or colon instead, because the time to the first sound grows with the first sentence. Markdown is dropped
and a piece with no letter or digit is not spoken. Talk's markdown handling is richer; the replies a phone agent
writes are plain sentences, so emphasis, code marks, links, headings and list marks are enough here.

`Piece.end` is where the piece ends in the reply as written (markdown counted), so a reply can be cut at what the
caller actually heard."""
from __future__ import annotations

import re
from dataclasses import dataclass

_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n\s*\n")
_CLAUSE = re.compile(r"[,;:]\s+")
_SAYS_SOMETHING = re.compile(r"[^\W_]")
_FULL_STOP = re.compile(r"[.!?]$")
_DOT = r"[^\n\r\u2028\u2029]"  # JavaScript's `.`: Python's also matches a carriage return


@dataclass(frozen=True)
class Piece:
    text: str  # what to say, with the markdown gone
    end: int  # where it ends in the raw reply


_FENCE = re.compile(r"```[\s\S]*?```")
_CODE = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD = re.compile(rf"(\*\*|__)({_DOT}*?)\1")
_ITALIC = re.compile(rf"(\*|_)({_DOT}*?)\1")
_STRIKE = re.compile(rf"~~({_DOT}*?)~~")
_HEADING = re.compile(rf"(?m)^\s{{0,3}}(#{{1,6}})\s+({_DOT}+)$")
_QUOTE = re.compile(r"(?m)^\s{0,3}>\s?")
_BULLET = re.compile(r"(?m)^\s*[-*+]\s+")
_NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+")
_RULE = re.compile(r"(?m)^\s*([-*_])\1{2,}\s*$")
_STRAY = re.compile(r"(^|\s)([*_~`#]{1,3})(\s|$)")
_SPACE_BEFORE_NEWLINE = re.compile(r"[ \t]+\n")
_BLANK_LINES = re.compile(r"\n{3,}")


def plain(raw: str) -> str:
    """Talk's markdownToSpeech (src/utils/markdownToSpeech.js), rule for rule and in the same order, so a caller
    hears what Talk would say: code fences are dropped, paired emphasis loses its marks (a lone underscore in a
    reference stays), headings get a pause mark."""
    s = _FENCE.sub(" ", raw)
    s = _CODE.sub(r"\1", s)
    s = _IMAGE.sub(r"\1", s)
    s = _LINK.sub(r"\1", s)
    s = _BOLD.sub(r"\2", s)
    s = _ITALIC.sub(r"\2", s)
    s = _STRIKE.sub(r"\1", s)
    s = _HEADING.sub(r"\2:", s)
    s = _QUOTE.sub("", s)
    s = _BULLET.sub("", s)
    s = _NUMBERED.sub("", s)
    s = _RULE.sub("", s)
    s = _STRAY.sub(r"\1\3", s)
    s = _SPACE_BEFORE_NEWLINE.sub("\n", s)
    return _BLANK_LINES.sub("\n\n", s).strip()


class SentenceSplitter:
    def __init__(self, first_clause_chars: int = 30) -> None:
        self._buffer = ""
        self._consumed = 0  # characters of the reply before `_buffer`
        self._joins: list[int] = []  # reply offsets where end_step left an unfinished clause: a step boundary
        self._first_clause = first_clause_chars
        self._opening = first_clause_chars > 0

    def _emit(self, raw: str, out: list[Piece]) -> None:
        text = plain(self._with_joins(raw))
        if _SAYS_SOMETHING.search(text):
            self._opening = False
            out.append(Piece(text, self._consumed + len(raw)))

    def _with_joins(self, raw: str) -> str:
        """`raw` as it is spoken: a step boundary with no white space on either side gets one, so the last word before
        a tool and the first after it don't fuse. Only the spoken copy changes; `Piece.end` counts the reply as written."""
        for at in reversed([j - self._consumed for j in self._joins]):
            if 0 < at < len(raw) and not raw[at - 1].isspace() and not raw[at].isspace():
                raw = raw[:at] + " " + raw[at:]
        return raw

    def _advance(self, count: int) -> None:
        self._consumed += count
        self._joins = [j for j in self._joins if j > self._consumed]

    def _next_cut(self) -> tuple[int, int] | None:
        """Where the next piece ends, and where the one after it starts."""
        sentence = _BOUNDARY.search(self._buffer)
        end = (sentence.start(), sentence.end()) if sentence else None
        if not self._opening:
            return end
        clause = _CLAUSE.search(self._buffer, self._first_clause)
        if not clause or (end and end[0] <= clause.start()):
            return end
        return clause.start() + 1, clause.end()

    def feed(self, delta: str) -> list[Piece]:
        """The pieces this delta completed, in order."""
        self._buffer += delta
        out: list[Piece] = []
        cut = self._next_cut()
        while cut:
            self._emit(self._buffer[: cut[0]], out)
            self._advance(cut[1])
            self._buffer = self._buffer[cut[1]:]
            cut = self._next_cut()
        return out

    def end_step(self) -> list[Piece]:
        """The model finished a stretch of text (before a tool call): speak what is pending now, but only if it ends
        a sentence. Talk would hold those words until the answer; a caller is better off hearing them at once. A
        clause still unfinished stays, since the text that follows the tool may well continue it."""
        raw = self._buffer.rstrip()
        out: list[Piece] = []
        if not _FULL_STOP.search(raw):
            if raw.strip():
                self._joins.append(self._consumed + len(self._buffer))
            return out
        self._emit(raw, out)
        self._advance(len(self._buffer))
        self._buffer = ""
        return out

    def flush(self) -> list[Piece]:
        """What is left when the turn is over."""
        out: list[Piece] = []
        raw = self._buffer.rstrip()
        if raw:
            self._emit(raw, out)
        self._advance(len(self._buffer))
        self._buffer = ""
        return out
