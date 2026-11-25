"""Talk's sentence cuts, in Python (src/lib/speech/sentences.js), so a phone call voices a reply at the same points
Talk does. A piece ends at a full stop, question or exclamation mark followed by white space, or at a blank line;
while nothing has been said yet, past `first_clause_chars` characters (0: never) the opening is cut at a comma,
semicolon or colon instead, because the time to the first sound grows with the first sentence. Markdown is dropped
by `plain()`, which is Talk's `markdownToSpeech` (src/utils/markdownToSpeech.js) rule for rule (R4.2), applied per
piece as Talk applies it, and a piece with no letter or digit is not spoken. The two change together: a rule added
to one is added to the other, with a case in server/tests/fixtures/speech/sentences_cases.json, which both test
suites run.

`Piece.end` is where the piece ends in the reply as written (markdown counted), so a reply can be cut at what the
caller actually heard."""
from __future__ import annotations

import re
from collections.abc import Callable
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


# Extensions spoken with "dot" whatever their case (R15.1), after a stem of two characters or more (R15.5), so the
# degrees B.Com and M.Com keep their full stop while ASP.NET is "ASP dot NET". Any other 2-5 character extension
# counts only in lowercase, after any stem, so a missing space before a capital ("hours.The") stays a full stop.
_KNOWN_EXTENSIONS = frozenset({
    "md", "pdf", "txt", "doc", "docx", "csv", "json", "html", "htm", "xml", "xls", "xlsx", "ppt", "pptx", "png",
    "jpg", "jpeg", "gif", "svg", "zip", "py", "js", "jsx", "ts", "tsx", "yml", "yaml", "toml", "log", "wav", "mp3",
    "mp4", "com", "org", "net", "io", "dev",
})
_LOWERCASE_EXTENSION = re.compile(r"[a-z][a-z0-9]{1,4}")
# Words joined by single dots with no space. A match may only start where a run of letters and digits starts:
# unanchored, it is retried from every letter of a long run, which is quadratic (R15.5).
_DOTTED = re.compile(r"(?<![^\W_])[^\W_]+(?:\.[^\W_]+)+")


@dataclass(frozen=True)
class _Emphasis:
    """One level of emphasis, as markdownToSpeech's BOLD and ITALIC. A star pairs with the next star on its line. An
    underscore opens only where no letter or digit comes before it and closes only where none comes after it
    (CommonMark's intraword rule), so the underscores in room_number or my_file_name are never taken as emphasis."""
    size: int
    mark: re.Pattern[str]
    star: re.Pattern[str]
    opens: re.Pattern[str]
    closes: re.Pattern[str]


_LINE_END = re.compile(r"[\n\r\u2028\u2029]")  # emphasis never spans one: what `_DOT` leaves out
_BOLD = _Emphasis(2, re.compile(r"\*\*|__"), re.compile(r"\*\*"),
                  re.compile(r"(?<![^\W_])__"), re.compile(r"__(?![^\W_])"))
_ITALIC = _Emphasis(1, re.compile(r"[*_]"), re.compile(r"\*"),
                    re.compile(r"(?<![^\W_])_"), re.compile(r"_(?![^\W_])"))


@dataclass(frozen=True)
class _LinkLike:
    """markdownToSpeech's IMAGE and LINK: the opener, and the least text between the brackets."""
    opener: re.Pattern[str]
    size: int
    min_text: int


_IMAGE = _LinkLike(re.compile(r"!\["), 2, 0)
_LINK = _LinkLike(re.compile(r"\["), 1, 1)
_CLOSE_BRACKET = re.compile(r"\]")
_CLOSE_PAREN = re.compile(r"\)")

_FENCE = re.compile(r"```[\s\S]*?```")
_CODE = re.compile(r"`([^`]+)`")
_INNER_UNDERSCORES = re.compile(r"(?<=[^\W_])_+(?=[^\W_])")
_STRIKE = re.compile(rf"~~({_DOT}*?)~~")
_HEADING = re.compile(rf"(?m)^\s{{0,3}}(#{{1,6}})\s+({_DOT}+)$")
_QUOTE = re.compile(r"(?m)^\s{0,3}>\s?")
# Line-start rules (markdownToSpeech's BULLET, NUMBERED, RULE): the tail is optional, so a match takes a whole run of
# white space and gives it back when no tail follows (_drop_at_line_start). Written `^\s*tail`, they retried every
# line start inside the run, which is quadratic on a run of newlines, and each met the same tail (R15.5).
_BULLET = re.compile(r"(?m)^(\s*)([-*+]\s+)?")
_NUMBERED = re.compile(r"(?m)^(\s*)(\d+\.\s+)?")
_RULE = re.compile(r"(?m)^(\s*)(([-*_])\3{2,}\s*$)?")
_STRAY = re.compile(r"(^|\s)([*_~`#]{1,3})(\s|$)")
_SPACE_BEFORE_NEWLINE = re.compile(r"(?<![ \t])[ \t]+\n")  # from a space run's start only: from each space was quadratic
_BLANK_LINES = re.compile(r"\n{3,}")


def _is_extension(word: str, stem: str) -> bool:
    return bool(_LOWERCASE_EXTENSION.fullmatch(word)) or (len(stem) >= 2 and word.lower() in _KNOWN_EXTENSIONS)


def _forward_search(pattern: re.Pattern[str], s: str) -> Callable[[int], int]:
    """markdownToSpeech's forwardSearch: where `pattern` next matches in `s`, at or after `start` (`len(s)`: nowhere).
    Asked with `start` never going back, so an answer still ahead is reused and each search starts past the last."""
    found = -1

    def search(start: int) -> int:
        nonlocal found
        if found < start:
            match = pattern.search(s, start)
            found = match.start() if match else len(s)
        return found

    return search


def _pair_emphasis(s: str, level: _Emphasis) -> str:
    r"""markdownToSpeech's pairEmphasis: keeps the text between paired marks exactly as the regex
    `\*\*(.*?)\*\*|(?<![^\W_])__(.*?)__(?![^\W_])` replaced by `\1\2` would (and the same with single marks), but in
    linear time. That regex retries every unclosed `_word` up to the end of its line, which is quadratic on a long
    line, and plain() runs on the event loop every call shares (R15.5)."""
    next_mark = _forward_search(level.mark, s)
    next_star = _forward_search(level.star, s)
    next_close = _forward_search(level.closes, s)
    next_line_end = _forward_search(_LINE_END, s)
    out: list[str] = []
    copied = 0
    at = next_mark(0)
    while at < len(s):
        body = at + level.size
        close = len(s)
        if s[at] == "*":
            close = next_star(body)
        elif level.opens.match(s, at):
            close = next_close(body)
        if close < next_line_end(body):
            out += (s[copied:at], s[body:close])
            copied = close + level.size
            at = next_mark(copied)
        else:
            at = next_mark(at + 1)
    out.append(s[copied:])
    return "".join(out)


def _keep_link_text(s: str, kind: _LinkLike) -> str:
    r"""markdownToSpeech's keepLinkText: keeps the text of each image or link, exactly as `!\[([^\]]*)\]\([^)]*\)` and
    `\[([^\]]+)\]\([^)]*\)` replaced by `\1` would, but in linear time. The text runs to the first `]`, a `(` must
    follow it at once, and the address runs to the first `)`. Those regexes send every unclosed `[` on to the end of
    the text looking for them (R15.5)."""
    next_opener = _forward_search(kind.opener, s)
    next_bracket = _forward_search(_CLOSE_BRACKET, s)
    next_paren = _forward_search(_CLOSE_PAREN, s)
    out: list[str] = []
    copied = 0
    at = next_opener(0)
    while at < len(s):
        text = at + kind.size
        close = next_bracket(text)
        end = next_paren(close + 2) if s[close + 1:close + 2] == "(" else len(s)
        if close - text >= kind.min_text and end < len(s):
            out += (s[copied:at], s[text:close])
            copied = end + 1
            at = next_opener(copied)
        else:
            at = next_opener(at + 1)
    out.append(s[copied:])
    return "".join(out)


def _drop_at_line_start(s: str, rule: re.Pattern[str]) -> str:
    """markdownToSpeech's dropAtLineStart: the white space and its tail go; white space with no tail stays."""
    return rule.sub(lambda m: m.group(1) if m.group(2) is None else "", s)


def _speak_dots(run: re.Match[str]) -> str:
    """markdownToSpeech's speakDots. A voice reads Architecture.md as a sentence ending before "MD", so the dots of a
    file name or a web address are spoken "dot". A dot is spoken when the word after it is an extension, or when
    that word starts with a letter and its own dot was spoken: "example" is too long for an extension, but
    www.example.co.uk is one address. The walk goes from the right so a spoken dot carries left, never right
    ("example.com.Then" keeps its full stop). Abbreviations (e.g.), decimals (3.5), versions (v2.3.0) and a missing
    space before a capital keep their dots."""
    words = run.group().split(".")
    parts = [words[-1]]  # right to left
    spoken = False
    for i in range(len(words) - 1, 0, -1):
        spoken = _is_extension(words[i], words[i - 1]) or (spoken and words[i][0].isalpha())
        parts += (" dot " if spoken else ".", words[i - 1])
    return "".join(reversed(parts))


def plain(raw: str) -> str:
    """Talk's markdownToSpeech (src/utils/markdownToSpeech.js), rule for rule and in the same order, so a caller
    hears what Talk would say: code fences are dropped, a file name or web address is read with "dot"
    (Architecture.md is "Architecture dot md"), emphasis loses its marks, each underscore inside a word is spoken
    ("room underscore number"), headings get a pause mark. Change it only together with markdownToSpeech, and add
    the shared case."""
    s = _FENCE.sub(" ", raw)
    s = _CODE.sub(r"\1", s)
    s = _keep_link_text(s, _IMAGE)
    s = _keep_link_text(s, _LINK)
    s = _pair_emphasis(s, _BOLD)
    s = _pair_emphasis(s, _ITALIC)
    s = _INNER_UNDERSCORES.sub(lambda run: " underscore" * len(run.group()) + " ", s)
    s = _DOTTED.sub(_speak_dots, s)  # after emphasis, so __init__.py is "init dot py"
    s = _STRIKE.sub(r"\1", s)
    s = _HEADING.sub(r"\2:", s)
    s = _QUOTE.sub("", s)
    s = _drop_at_line_start(s, _BULLET)
    s = _drop_at_line_start(s, _NUMBERED)
    s = _drop_at_line_start(s, _RULE)
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
