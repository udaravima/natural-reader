"""Server-side text extraction (A1 spec §4).

The chunk layout must equal the reader's (src/utils/segmentation.js,
`chunkLayout`): a citation carries a chunk's `page`, and the reader jumps to
ITS page with that number. So this module replicates the reader's rules
exactly — JavaScript's whitespace class, UTF-16 string lengths, mdast block
boundaries and mdast-util-to-string's text — and the committed fixtures in
server/tests/fixtures/segmentation pin it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin

SENTENCES_PER_PAGE = 40  # src/constants.js SENTENCES_PER_TEXT_PAGE

# JavaScript's \s and String.prototype.trim set. Python's \s differs both ways:
# it lacks U+FEFF and adds \x1c-\x1f.
_JS_WS_CHARS = (
    "\t\n\v\f\r \xa0\u1680"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_WS_CLASS = "[" + re.escape(_JS_WS_CHARS) + "]"
_WS_RUN = re.compile(_WS_CLASS + "+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])" + _WS_CLASS + "+")
_TASK_MARKER = re.compile(r"^\[[ xX]\][ \t]+")  # GFM task item; mdast drops it and the space


@dataclass(frozen=True)
class Chunk:
    ord: int
    page: int
    chunk_type: str
    text: str


@dataclass(frozen=True)
class Extraction:
    chunks: list[Chunk]
    page_count: int


def _js_trim(s: str) -> str:
    return s.strip(_JS_WS_CHARS)


def _js_len(s: str) -> int:
    """String.length: UTF-16 code units, so an emoji counts 2."""
    return len(s.encode("utf-16-le")) // 2


def _collapse(s: str) -> str:
    return _js_trim(_WS_RUN.sub(" ", s))


# ---------- plain text (usePdfEngine segmentSentences + paginateSentences) ----------

def _segment_sentences(raw: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(_WS_RUN.sub(" ", raw)) if _js_len(_js_trim(s)) > 5]


def extract_text(raw: str) -> Extraction:
    sentences = _segment_sentences(raw)
    pages = [sentences[i:i + SENTENCES_PER_PAGE]
             for i in range(0, len(sentences), SENTENCES_PER_PAGE)] or [[]]
    chunks: list[Chunk] = []
    for pi, page in enumerate(pages):
        text = _collapse(" ".join(page))
        if text:
            chunks.append(Chunk(len(chunks), pi + 1, "page", text))
    return Extraction(chunks, len(pages))


# ---------- markdown (segmentMarkdown + paginateMarkdownBlocks + splitSentences) ----------

def _split_sentences(text: str) -> list[str]:
    return [t for t in (_js_trim(s) for s in _SENT_SPLIT.split(_collapse(text))) if t]


def _parser() -> MarkdownIt:
    # CommonMark + the GFM parts that change block structure or text:
    # tables, strikethrough, footnotes. Task items are handled in _inline_text.
    return MarkdownIt("commonmark").enable("table").enable("strikethrough").use(footnote_plugin)


def _strip_one_newline(s: str) -> str:
    return s[:-1] if s.endswith("\n") else s


def _inline_text(inline, strip_task: bool) -> str:
    """mdast-util-to-string over one inline run: text values concatenated with
    no separators; soft breaks stay "\n"; hard breaks and footnote refs add
    nothing; images contribute their alt text; inline html its raw source."""
    parts: list[str] = []
    for i, c in enumerate(inline.children or []):
        if c.type in ("text", "text_special", "code_inline", "html_inline"):
            s = c.content
            if strip_task and i == 0 and c.type == "text":
                s = _TASK_MARKER.sub("", s, count=1)
            parts.append(s)
        elif c.type == "softbreak":
            parts.append("\n")
        elif c.type == "image":
            parts.append(c.content)
    return "".join(parts)


def _block_text(tokens) -> str:
    parts: list[str] = []
    for i, t in enumerate(tokens):
        if t.type == "inline":
            strip_task = (i >= 2 and tokens[i - 1].type == "paragraph_open"
                          and tokens[i - 2].type == "list_item_open")
            parts.append(_inline_text(t, strip_task))
        elif t.type in ("fence", "code_block", "html_block"):
            parts.append(_strip_one_newline(t.content))
    return "".join(parts)


def _top_level_blocks(tokens):
    """Yield (first_token, tokens_of_block) per top-level block, skipping the
    footnote block (mdast skips footnoteDefinition; markdown-it collects
    footnotes at the end). Link reference definitions emit no tokens, just as
    mdast's `definition` nodes are skipped by the reader."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.nesting == 1:
            j = i + 1
            while not (tokens[j].nesting == -1 and tokens[j].level == t.level):
                j += 1
            if t.type != "footnote_block_open":
                yield t, tokens[i:j + 1]
            i = j + 1
        else:
            yield t, [t]
            i += 1


def extract_markdown(raw: str) -> Extraction:
    blocks = []  # (plain, sentence_count, is_code)
    for first, toks in _top_level_blocks(_parser().parse(raw)):
        is_code = first.type in ("fence", "code_block")
        plain = "" if is_code else _block_text(toks)
        blocks.append((plain, 0 if is_code else len(_split_sentences(plain)), is_code))
    # Greedy packing: close a page when the next block would push it past the
    # cap. Zero-sentence blocks (code, hr) still occupy the page they land on.
    pages: list[list[tuple[str, int, bool]]] = []
    current: list[tuple[str, int, bool]] = []
    count = 0
    for block in blocks:
        if current and count + block[1] > SENTENCES_PER_PAGE:
            pages.append(current)
            current, count = [], 0
        current.append(block)
        count += block[1]
    if current:
        pages.append(current)
    pages = pages or [[]]
    chunks: list[Chunk] = []
    for pi, page in enumerate(pages):
        for plain, _n, is_code in page:
            if is_code:
                continue
            text = _collapse(plain)
            if text:
                chunks.append(Chunk(len(chunks), pi + 1, "block", text))
    return Extraction(chunks, len(pages))


# ---------- PDF (one chunk per non-empty page, like pdf.js in the reader) ----------

def _pdf_page_texts(path: Path) -> tuple[list[str], int]:
    import pypdfium2 as pdfium  # imported lazily: only PDF jobs pay for it

    pdf = pdfium.PdfDocument(str(path))
    try:
        texts = []
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            try:
                texts.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
        return texts, len(pdf)
    finally:
        pdf.close()


def extract_pdf(path: Path) -> Extraction:
    texts, page_count = _pdf_page_texts(path)
    chunks: list[Chunk] = []
    for pi, raw in enumerate(texts):
        text = _collapse(raw.replace("\x00", ""))  # Postgres TEXT cannot hold NUL
        if text:
            chunks.append(Chunk(len(chunks), pi + 1, "page", text))
    return Extraction(chunks, page_count)


def extract_file(path: Path, file_type: str) -> Extraction:
    if file_type == "pdf":
        return extract_pdf(path)
    if file_type not in ("text", "markdown"):
        raise ValueError(f"unsupported file_type {file_type!r}")
    raw = Path(path).read_bytes().decode("utf-8-sig")  # BOM stripped, like FileReader
    return extract_markdown(raw) if file_type == "markdown" else extract_text(raw)
