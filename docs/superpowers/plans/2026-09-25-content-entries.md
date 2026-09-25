# Document Content vs Library Entries (A1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split documents into ownerless, server-verified *content* and per-user *library entries*, so identical files dedupe without rework, nobody can delete a document for everyone, and a known `doc_id` is never access.

**Architecture:** `documents` becomes content keyed by the server's SHA-256 of uploaded bytes. `library_entries` (new) records who holds it; `project_documents` (C0) records which projects hold it. Every registration uploads bytes. The server hashes them, dedupes on a hash match, and extracts text itself, using segmentation that replicates the reader's exactly. Content nobody references is garbage-collected by one helper. Content-changing operations are allowed only for the sole holder or an admin.

**Tech Stack:** FastAPI, psycopg3 async, Postgres 16 + pgvector, `pypdfium2`, `markdown-it-py` + `mdit-py-plugins`, React/Vite, Vitest, pytest (asyncio auto mode).

**Spec:** [docs/superpowers/specs/2026-09-25-document-content-entries-design.md](../specs/2026-09-25-document-content-entries-design.md)

## Global Constraints

- **Open source:** nothing deployment-specific is hardcoded. New knobs go in `.env.example` with safe defaults:
  - `DOC_STORAGE_DIR`: falls back to `PDF_STORAGE_DIR`, then `./data/pdfs`;
  - `TEXT_UPLOAD_MAX_MB=10`;
  - `PDF_UPLOAD_MAX_MB=50`;
  - `LOG_AUDIT_FILE`: default `<LOG_DIR>/audit.log`.
- **Licenses:** MIT-compatible dependencies only: `pypdfium2` (Apache-2.0/BSD-3), `markdown-it-py` (MIT), `mdit-py-plugins` (MIT). **Never PyMuPDF (AGPL).**
- **Refusals:** every refusal that A1 adds or changes uses `server.http_errors.refusal(status, error, message, **extra)`. FastAPI wraps them, so the body is `{"detail": {"error": …, "message": …, …}}`.
  - Plain-string 404s in code A1 doesn't otherwise change stay as they are. The SPA mapper handles both shapes. Ruling: converting every old route is churn with no user-visible change.
  - Not allowed = **404** (A1 has no 403s).
  - Content ops refused = **409 `content_shared`** with `reason` ∈ `other_holders` | `in_project`.
- **Access checks:** resolved in SQL via `readable_docs_where` / `readable_docs_params`, never by filtering in Python.
- **Logging:** no personal data at INFO or above. Log user, doc and project IDs only; never emails, names, file names or search text. State changes go through `server.audit.audit(...)`.
- **Reader parity:** the reader (`src/utils/segmentation.js`) is the authority for chunk layout. When the Python output differs from the committed fixtures, fix the Python, never the fixtures.
- **Invisible characters:** write them as source escapes (` `, `﻿`, `\x1c`), never as literal characters. Editors and tools silently strip or normalize literals. After writing `extract.py`, `test_extract.py` or the fixture generator, this must print nothing: `grep -nP "[\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}\x{feff}\x{1c}]" <file>`. The generated fixture *inputs* are the exception: they must contain the literal characters.
- **Commits:** the user's global rule is that nothing is committed or pushed without their explicit permission. Every "Commit" step below runs only after the user has approved commits for this plan. Ask once before Task 1 if that approval isn't already on record.
- **Don't deploy `development` between Task 6 and Task 12.** The API and the SPA change shape across those tasks and only line up again at Task 12.
- **Commands:**
  - Postgres first: `env -u XDG_DATA_HOME podman-compose -f docker-compose.yml up -d postgres`
  - Backend tests: `.venv/bin/python -m pytest server/tests -q`
  - Frontend tests: `npx vitest run`
  - Lint: `npm run lint`. The only existing error is `src/hooks/useAuth.js:51`; add no new ones.

## Review Focus

These five input classes are implied by the spec, but no spec test exercises them. Each one's test lives in the task named.

1. **Empty upload (0 bytes)** → 422 `empty_file`; no content row, no entry, no staged file left behind. *(Task 3; route-level in Task 9)*
2. **Corrupt or encrypted PDF that passes the `%PDF-` magic check** → the upload succeeds (202), then the doc ends in `state='failed'` with a readable `error_message`. No 5xx, and the entry is kept. *(Task 8)*
3. **NUL bytes.**
   - A text or markdown file containing `\x00` → 415. Postgres TEXT can't store NUL.
   - NULs in text that `pypdfium2` extracts from a PDF are stripped, so the insert can't fail. *(Tasks 2, 3)*
4. **File names:**
   - `REPORT.PDF` detects as PDF (the check is case-insensitive);
   - a name with no extension falls back to PDF detection by magic bytes;
   - `../../x.pdf` is stored only as a display name, and the bytes path is always `<dir>/<doc_id>.<ext>`. *(Task 3)*
5. **Re-upload by a holder:**
   - the same user uploading the same bytes twice → 200 dedupe, still one entry, tags replaced (not duplicated);
   - a user holding a `shared` entry who uploads the bytes → the entry is upgraded to `upload`. *(Task 9)*

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/utils/segmentation.js` (new) | the reader's sentence/markdown segmentation + `chunkLayout()`; moved out of `usePdfEngine.js` | 1 |
| `src/utils/segmentation.fixtures.test.js` (new) | generates / asserts committed parity fixtures | 1 |
| `server/tests/fixtures/segmentation/*` (new) | shared inputs + JS-generated expected layouts | 1 |
| `server/services/extract.py` (new) | server extraction replicating the reader | 2 |
| `server/tests/pdfgen.py` (new) | tiny valid-PDF builder for tests | 2 |
| `server/http_errors.py` (new) | `refusal()` helper | 3 |
| `server/services/doc_storage.py` (new) | stage upload → hash, size cap, type check → place / discard | 3 |
| `server/audit.py` (new) + `server/logging_config.py` | `server.audit` logger + `audit()` | 4 |
| `server/tests/seed.py` (new) | the only place tests write documents / entries / placements | 5 |
| `server/sql/011_content_entries.sql` (new) | schema cut | 6 |
| `server/services/doc_content.py` (new) | entries, shares, sole-holder rule, GC, sweep, state recovery | 6, 7 |
| `server/auth/authz.py` | new read predicate, `assert_holds_upload`, `can_manage_project_docs` | 6 |
| `server/routers/docs.py` | routes over the new model; multipart register; gates | 6, 8, 9, 10 |
| `server/routers/projects.py` | link/unlink/delete over entries + GC | 6, 7 |
| `server/routers/admin.py` | user deletion GCs held content | 6 |
| `server/db.py` | startup: state recovery + orphan sweep | 7 |
| `server/services/doc_pipeline.py` (new) | per-doc lock, extract → embed job, legacy swap, chunk replace | 8 |
| `server/tests/docs_harness.py` (new) | app + pool shim + fake embed for pipeline-level route tests | 8 |
| `src/lib/apiErrors.js` (new) | refusal → notice mapper | 11 |
| `src/lib/docMeta.js`, `src/App.jsx`, `src/components/IndexButton.jsx` | multipart register, new states, convert without byte upload | 11 |
| `src/lib/uploadPdf.js` | **deleted** | 11 |
| `src/components/library/LibraryPage.jsx` | remove-from-library, badges, chip rules | 12 |
| `.env.example`, `docs/LIBRARY.md`, `docs/ARCHITECTURE.md`, `README.md` | config + docs | 13 |

---

### Task 1: Reader segmentation module + committed parity fixtures

The server must reproduce the reader's chunk layout exactly (spec §4). The reader's segmentation is private to `usePdfEngine.js` today. This task moves it, unchanged, into an importable module and commits JS-generated expected layouts that the Python side (Task 2) must match.

**Files:**
- Create: `src/utils/segmentation.js`
- Modify: `src/hooks/usePdfEngine.js` (remove the moved functions, import them)
- Create: `src/utils/segmentation.fixtures.test.js`
- Create: `server/tests/fixtures/segmentation/{plain.txt,loose-list.md,mixed.md}` + `*.expected.json`

**Interfaces:**
- Produces: `chunkLayout(fileType: 'text'|'markdown', rawText: string) → Array<{ord:number, page:number, chunk_type:'page'|'block', text:string}>`. It also exports `segmentSentences, paginateSentences, splitSentences, segmentMarkdown, buildMarkdownPage, paginateMarkdownBlocks`.
- Produces: fixture files `server/tests/fixtures/segmentation/<name>.<ext>` + `<name>.<ext>.expected.json`, consumed by Task 2.

- [ ] **Step 1: Move the segmentation code verbatim**

  Create `src/utils/segmentation.js`. Cut these definitions out of `src/hooks/usePdfEngine.js` (currently lines ~17–122) and paste them **byte-for-byte**, adding `export`:
  - `segmentSentences`
  - `paginateSentences`
  - `splitSentences`
  - `segmentMarkdown`
  - `buildMarkdownPage`
  - `paginateMarkdownBlocks`

  Move the four mdast imports and the `SENTENCES_PER_TEXT_PAGE` import with them. Keep every comment. Then append:

```js
/**
 * The chunk layout the server must reproduce (server/services/extract.py) so a
 * citation's `page` lands on the page this reader shows. Mirrors the text and
 * markdown branches of usePdfEngine's extractAllChunks exactly — same
 * segmentation, same pagination, same text normalization, same skips.
 */
export function chunkLayout(fileType, rawText) {
    const out = [];
    let ord = 0;
    if (fileType === 'text') {
        const pages = paginateSentences(segmentSentences(rawText), SENTENCES_PER_TEXT_PAGE);
        pages.forEach((sentences, pi) => {
            const text = (sentences || []).join(' ').replace(/\s+/g, ' ').trim();
            if (text) out.push({ ord: ord++, page: pi + 1, chunk_type: 'page', text });
        });
        return out;
    }
    if (fileType === 'markdown') {
        const pages = paginateMarkdownBlocks(segmentMarkdown(rawText), SENTENCES_PER_TEXT_PAGE);
        pages.forEach((pageObj, pi) => {
            for (const block of pageObj.blocks || []) {
                if (block.type === 'code') continue;
                const text = (block.plain || '').replace(/\s+/g, ' ').trim();
                if (text) out.push({ ord: ord++, page: pi + 1, chunk_type: 'block', text });
            }
        });
        return out;
    }
    return out;
}
```

  In `usePdfEngine.js`, add `import { segmentSentences, paginateSentences, segmentMarkdown, paginateMarkdownBlocks } from '../utils/segmentation';`. Add any other moved names the hook still uses. Remove the mdast imports only if the hook no longer uses them.

- [ ] **Step 2: Verify the refactor changed nothing**

  Run: `npx vitest run && npm run lint`
  Expected: the same pass count as before, and no new lint errors.

- [ ] **Step 3: Write the fixture inputs**

  Run this once. It writes deterministic inputs that exercise every divergence risk named in the spec:

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
d = Path("server/tests/fixtures/segmentation"); d.mkdir(parents=True, exist_ok=True)

# plain.txt — BOM, CRLF, short sentences (<=5 JS chars are dropped), an emoji
# sentence (JS counts UTF-16 units), NBSP, a mid-text U+FEFF (JS \s, not Python \s),
# \x1c (Python \s, not JS \s), and >80 sentences so pagination (40/page) matters.
lines = ["\ufeffIntro line one is here. Ok. Yes! Short one. \U0001F600\U0001F600\U0001F600."]
lines.append("Tab\tseparated\u00a0words sit here. Zero\ufeffwidth joins here. Odd\x1cseparator here.")
for i in range(1, 86):
    lines.append(f"Sentence number {i} talks about topic {i % 7}.")
(d / "plain.txt").write_bytes("\r\n".join(lines).encode("utf-8"))

# loose-list.md — a loose list (blank lines between items) is ONE mdast node;
# enough sentences that the list and following paragraphs straddle pages.
items = "\n\n".join(f"- Item {i} says one thing. Item {i} says another." for i in range(1, 16))
paras = "\n\n".join(" ".join(f"Para {p} sentence {s}." for s in range(1, 9)) for p in range(1, 8))
(d / "loose-list.md").write_text(f"# Loose list\n\n{items}\n\n{paras}\n", encoding="utf-8")

# mixed.md — code fence FIRST (a zero-sentence block that still occupies page 1),
# footnotes (ref + multi-line def), link reference definition, task list, html
# block, image alt, hard + soft breaks, entities, blockquote, table, thematic
# break, nested code in a list, and one oversize paragraph (45 sentences).
big = " ".join(f"Big {i} goes on." for i in range(1, 46))
mixed = f"""```python
print("first block is code")
```

# Heading one

Some text with a footnote[^n] and an entity &amp; more. Second sentence here.

[^n]: The note body.
    It continues here.

[ref]: https://example.com "Title"

- [ ] Buy milk today.
- [x] Walk the dog now.

<div>Raw html block. With two sentences.</div>

![An image alt.](pic.png) Trailing words here.

Hard break line  
next line. Soft break
continues here.

> Quoted wisdom. Also quoted.

| Col A | Col B |
|---|---|
| Cell one. | Cell two. |

---

1. Step with code:

   ```
   nested code line.
   ```

2. Final step here.

{big}

Tail paragraph after the big one. It closes the file.
"""
(d / "mixed.md").write_text(mixed, encoding="utf-8")
EOF
```

- [ ] **Step 4: Write the fixture test (generate + assert)**

  Create `src/utils/segmentation.fixtures.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { chunkLayout } from './segmentation';

// Shared with server/tests/test_extract.py: the Python extractor must produce
// these exact layouts. Regenerate ONLY when the reader's segmentation changes:
//   UPDATE_SEGMENTATION_FIXTURES=1 npx vitest run src/utils/segmentation.fixtures.test.js
const DIR = new URL('../../server/tests/fixtures/segmentation/', import.meta.url);
const TYPES = { txt: 'text', md: 'markdown' };

// Same decode the reader gets from FileReader.readAsText: UTF-8, BOM stripped.
const decode = (buf) => new TextDecoder('utf-8').decode(buf);

describe('segmentation parity fixtures', () => {
    const inputs = readdirSync(DIR).filter((f) => /\.(txt|md)$/.test(f));
    it('has inputs', () => expect(inputs.length).toBeGreaterThan(0));
    for (const name of inputs) {
        it(`${name} matches its committed layout`, () => {
            const ext = name.split('.').pop();
            const layout = chunkLayout(TYPES[ext], decode(readFileSync(new URL(name, DIR))));
            const expectedUrl = new URL(`${name}.expected.json`, DIR);
            if (process.env.UPDATE_SEGMENTATION_FIXTURES) {
                writeFileSync(expectedUrl, JSON.stringify(layout, null, 1) + '\n');
            }
            expect(layout).toEqual(JSON.parse(readFileSync(expectedUrl, 'utf-8')));
        });
    }
});
```

- [ ] **Step 5: Generate the expected layouts, then assert them**

  Run: `UPDATE_SEGMENTATION_FIXTURES=1 npx vitest run src/utils/segmentation.fixtures.test.js`, then `npx vitest run src/utils/segmentation.fixtures.test.js`.
  Expected: PASS both times. Open `mixed.md.expected.json` and check that the first chunk has `page: 1` and that the oversize "Big …" paragraph sits alone on its own page. Also open `plain.txt.expected.json` and check that it spans three pages and that "Ok." / "Yes!" appear in no chunk.

- [ ] **Step 6: Commit** (only with the user's approval, per Global Constraints)

```bash
git add src/utils/segmentation.js src/utils/segmentation.fixtures.test.js src/hooks/usePdfEngine.js server/tests/fixtures/segmentation
git commit -m "refactor(reader): export segmentation + commit chunk-layout parity fixtures"
```

---

### Task 2: Server-side extraction that replicates the reader

**Files:**
- Create: `server/services/extract.py`
- Create: `server/tests/pdfgen.py`
- Create: `server/tests/test_extract.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: Task 1 fixtures.
- Produces:
  - `Chunk(ord:int, page:int, chunk_type:str, text:str)` (frozen dataclass);
  - `Extraction(chunks:list[Chunk], page_count:int)`;
  - `extract_file(path: Path, file_type: str) -> Extraction`. It is synchronous; call it via `asyncio.to_thread`;
  - `extract_text(text) -> Extraction`, `extract_markdown(text) -> Extraction`, `extract_pdf(path) -> Extraction`;
  - `make_pdf(page_texts: list[str]) -> bytes` in `server/tests/pdfgen.py`.

- [ ] **Step 1: Add dependencies**

  In `requirements.txt`, after the `python-multipart` line, add:

```
# Server-side text extraction (A1): PDF text per page, and a CommonMark+GFM
# parser whose top-level blocks match the reader's mdast. All MIT-compatible;
# never PyMuPDF (AGPL).
pypdfium2>=4.0
markdown-it-py>=3.0
mdit-py-plugins>=0.4
```

  Run: `.venv/bin/pip install -r requirements.txt` (installs `mdit-py-plugins`; the others are already present).

- [ ] **Step 2: Write the PDF builder for tests**

  Create `server/tests/pdfgen.py`:

```python
"""Build a tiny valid PDF (Helvetica, one text line per page) for tests. An
empty string makes a page with no text. Keeps binary fixtures out of git."""
from __future__ import annotations


def make_pdf(page_texts: list[str]) -> bytes:
    n = len(page_texts)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(page_texts):
        esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({esc}) Tj ET".encode("latin-1") if text else b""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)
```

- [ ] **Step 3: Write the failing tests**

  Create `server/tests/test_extract.py`:

```python
"""Server extraction must equal the reader's chunk layout (A1 spec §4). The
expected layouts are generated by the reader's own code (Task 1) — when this
fails, fix server/services/extract.py, never the fixtures."""
import json
import re
from pathlib import Path

import pytest

from server.services import extract
from server.tests.pdfgen import make_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "segmentation"
JS_WS = re.compile(r"[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]")


def _cases():
    return sorted(p for p in FIXTURES.iterdir() if p.suffix in (".txt", ".md"))


@pytest.mark.parametrize("src", _cases(), ids=lambda p: p.name)
def test_layout_matches_reader(src):
    expected = json.loads((FIXTURES / f"{src.name}.expected.json").read_text("utf-8"))
    ftype = "text" if src.suffix == ".txt" else "markdown"
    got = extract.extract_file(src, ftype).chunks
    assert [(c.ord, c.page, c.chunk_type) for c in got] == \
        [(e["ord"], e["page"], e["chunk_type"]) for e in expected]
    for c, e in zip(got, expected):
        if ftype == "text":
            assert c.text == e["text"]
        else:  # markdown text may differ only in whitespace placement
            assert JS_WS.sub("", c.text) == JS_WS.sub("", e["text"])


def test_pdf_one_chunk_per_non_empty_page(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(make_pdf(["Page one text.", "", "Page three text."]))
    result = extract.extract_pdf(p)
    assert result.page_count == 3
    assert [(c.ord, c.page, c.chunk_type) for c in result.chunks] == [(0, 1, "page"), (1, 3, "page")]
    assert "Page one text." in result.chunks[0].text


def test_pdf_text_never_contains_nul(tmp_path, monkeypatch):
    p = tmp_path / "x.pdf"
    p.write_bytes(make_pdf(["abc"]))
    monkeypatch.setattr(extract, "_pdf_page_texts", lambda path: (["a\x00b."], 1))
    assert extract.extract_pdf(p).chunks[0].text == "ab."


def test_text_page_count_is_reader_page_count():
    result = extract.extract_text("One two three. " * 3)
    assert result.page_count == 1


def test_unknown_type_rejected(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract.extract_file(p, "docx")
```

  Run: `.venv/bin/python -m pytest server/tests/test_extract.py -q`
  Expected: FAIL (`ModuleNotFoundError: server.services.extract`).

- [ ] **Step 4: Implement `server/services/extract.py`**

```python
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
    "\t\n\v\f\r \u00a0\u1680"
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
```

- [ ] **Step 5: Run the tests; fix divergences in Python only**

  Run: `.venv/bin/python -m pytest server/tests/test_extract.py -q`
  Expected: PASS. If a markdown case fails, print the first differing block from both sides and adjust `_inline_text` / `_block_text` / `_top_level_blocks`. For example, if markdown-it and mdast disagree on a table or footnote edge, handle that token type. Record each such adjustment as a one-line comment naming the mdast behaviour it mirrors.

- [ ] **Step 6: Commit** (with approval)

```bash
git add requirements.txt server/services/extract.py server/tests/pdfgen.py server/tests/test_extract.py
git commit -m "feat(docs): server-side extraction replicating the reader's chunk layout"
```

---

### Task 3: Upload staging, storage and the refusal helper

**Files:**
- Create: `server/http_errors.py`
- Create: `server/services/doc_storage.py`
- Create: `server/tests/test_doc_storage.py`

**Interfaces:**
- Produces: `refusal(status:int, error:str, message:str, **extra) -> HTTPException`.
- Produces (`doc_storage`):
  - `storage_dir() -> Path` and `type_from_name(name) -> 'pdf'|'text'|'markdown'`;
  - `Staged(path, sha256, size, file_type)`;
  - `async stage_upload(upload: UploadFile, file_name: str) -> Staged`, which raises refusals 413 `too_large` (with `limit_mb`), 415 `unsupported_type` and 422 `empty_file`;
  - `place(staged, doc_id) -> Path`, `discard(staged) -> None` (idempotent), and `sha256_file(path) -> str | None` (None if the file is missing).

- [ ] **Step 1: Write the failing tests**

  Create `server/tests/test_doc_storage.py`:

```python
import hashlib
import io

import pytest
from fastapi import HTTPException, UploadFile

from server.services import doc_storage
from server.tests.pdfgen import make_pdf


@pytest.fixture(autouse=True)
def _dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("PDF_STORAGE_DIR", raising=False)
    return tmp_path / "store"


def _upload(data: bytes, name="x.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_storage_dir_fallbacks(monkeypatch, tmp_path):
    monkeypatch.delenv("DOC_STORAGE_DIR")
    monkeypatch.setenv("PDF_STORAGE_DIR", str(tmp_path / "legacy"))
    assert doc_storage.storage_dir() == (tmp_path / "legacy").resolve()
    monkeypatch.delenv("PDF_STORAGE_DIR")
    assert doc_storage.storage_dir().name == "pdfs"


@pytest.mark.parametrize("name,expected", [
    ("a.md", "markdown"), ("A.MARKDOWN", "markdown"), ("notes.TXT", "text"),
    ("REPORT.PDF", "pdf"), ("no_extension", "pdf"), ("../../x.pdf", "pdf"),
])
def test_type_from_name(name, expected):
    assert doc_storage.type_from_name(name) == expected


async def test_stage_hashes_and_places(_dir):
    data = make_pdf(["hello"])
    staged = await doc_storage.stage_upload(_upload(data, "../../evil.pdf"), "../../evil.pdf")
    assert staged.sha256 == hashlib.sha256(data).hexdigest() and staged.size == len(data)
    final = doc_storage.place(staged, staged.sha256)
    assert final == _dir.resolve() / f"{staged.sha256}.pdf" and final.read_bytes() == data
    doc_storage.discard(staged)  # idempotent after place


async def test_pdf_without_magic_is_415():
    with pytest.raises(HTTPException) as e:
        await doc_storage.stage_upload(_upload(b"not a pdf"), "x.pdf")
    assert e.value.status_code == 415 and e.value.detail["error"] == "unsupported_type"


async def test_text_must_be_utf8_without_nul():
    for bad in (b"\xff\xfe bad", b"ok\x00text"):
        with pytest.raises(HTTPException) as e:
            await doc_storage.stage_upload(_upload(bad, "a.txt"), "a.txt")
        assert e.value.status_code == 415


async def test_empty_file_is_422_and_leaves_no_staging(_dir):
    with pytest.raises(HTTPException) as e:
        await doc_storage.stage_upload(_upload(b"", "a.txt"), "a.txt")
    assert e.value.status_code == 422 and e.value.detail["error"] == "empty_file"
    assert not any((_dir / ".staging").glob("*"))


async def test_size_cap_413(monkeypatch):
    monkeypatch.setenv("TEXT_UPLOAD_MAX_MB", "1")
    with pytest.raises(HTTPException) as e:
        await doc_storage.stage_upload(_upload(b"a" * (1024 * 1024 + 1), "a.txt"), "a.txt")
    assert e.value.status_code == 413 and e.value.detail["limit_mb"] == 1


def test_sha256_file_missing_is_none(tmp_path):
    assert doc_storage.sha256_file(tmp_path / "nope") is None
```

  Run: `.venv/bin/python -m pytest server/tests/test_doc_storage.py -q` → FAIL (module missing).

- [ ] **Step 2: Implement `server/http_errors.py`**

```python
"""One shape for every refusal (A1 spec §7): FastAPI returns
{"detail": {"error": <code>, "message": <human text>, ...extra}}. The SPA's
src/lib/apiErrors.js maps `error` (and `reason`) to the notice it shows."""
from __future__ import annotations

from fastapi import HTTPException


def refusal(status: int, error: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error, "message": message, **extra})
```

- [ ] **Step 3: Implement `server/services/doc_storage.py`**

```python
"""Content bytes on disk (A1 spec §4, §9).

An upload streams into a staging file while it is hashed; the server's
SHA-256 is the doc id. The staged file moves into place (`<dir>/<doc_id>.<ext>`)
only after the caller's INSERT has committed (spec §3, races), so a
concurrent GC can never delete a file a newer upload just wrote. The user's
file name is display-only and never part of a path.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from ..http_errors import refusal

MIB = 1024 * 1024
_EXT = {"pdf": "pdf", "text": "txt", "markdown": "md"}


def storage_dir() -> Path:
    raw = os.environ.get("DOC_STORAGE_DIR") or os.environ.get("PDF_STORAGE_DIR") or "./data/pdfs"
    return Path(raw).resolve()


def _cap_mb(file_type: str) -> int:
    if file_type == "pdf":
        return int(os.environ.get("PDF_UPLOAD_MAX_MB", "50"))
    return int(os.environ.get("TEXT_UPLOAD_MAX_MB", "10"))


def type_from_name(name: str) -> str:
    """Same rule as the SPA's detectFileType: .md/.markdown, .txt, else PDF."""
    lower = (name or "").lower()
    if lower.endswith((".md", ".markdown")):
        return "markdown"
    if lower.endswith(".txt"):
        return "text"
    return "pdf"


@dataclass
class Staged:
    path: Path
    sha256: str
    size: int
    file_type: str


def _unsupported():
    return refusal(415, "unsupported_type", "Unsupported file type.")


def _check_type(path: Path, file_type: str, head: bytes) -> None:
    if file_type == "pdf":
        if not head.startswith(b"%PDF-"):
            raise _unsupported()
        return
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise _unsupported() from None
    if "\x00" in text:  # binary disguised as text; Postgres TEXT can't hold NUL
        raise _unsupported()


async def stage_upload(upload: UploadFile, file_name: str) -> Staged:
    file_type = type_from_name(file_name)
    cap_mb = _cap_mb(file_type)
    staging = storage_dir() / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(tmp, "wb") as out:
            while chunk := await upload.read(MIB):
                size += len(chunk)
                if size > cap_mb * MIB:
                    raise refusal(413, "too_large", f"File exceeds the {cap_mb} MB limit.",
                                  limit_mb=cap_mb)
                if len(head) < 5:
                    head += chunk[: 5 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise refusal(422, "empty_file", "The file is empty.")
        _check_type(tmp, file_type, head)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return Staged(tmp, digest.hexdigest(), size, file_type)


def place(staged: Staged, doc_id: str) -> Path:
    base = storage_dir()
    final = (base / f"{doc_id}.{_EXT[staged.file_type]}").resolve()
    if not final.is_relative_to(base):  # defense in depth: doc_id is our own hex digest
        raise ValueError("storage path escaped DOC_STORAGE_DIR")
    os.replace(staged.path, final)  # atomic within one filesystem
    return final


def discard(staged: Staged) -> None:
    staged.path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(MIB):
                digest.update(block)
        return digest.hexdigest()
    except FileNotFoundError:
        return None
```

- [ ] **Step 4: Run the tests** — `.venv/bin/python -m pytest server/tests/test_doc_storage.py -q` → PASS.

- [ ] **Step 5: Commit** (with approval)

```bash
git add server/http_errors.py server/services/doc_storage.py server/tests/test_doc_storage.py
git commit -m "feat(docs): upload staging with server hash, type check and size caps"
```

---

### Task 4: Audit logger

**Files:**
- Create: `server/audit.py`
- Modify: `server/logging_config.py`
- Modify: `server/tests/test_logging_config.py`

**Interfaces:**
- Produces: `audit(event: str, **fields) -> None`. It logs at INFO to the `server.audit` logger as `"<event> k=v k=v"`.
- Event names used later: `entry.added`, `entry.removed`, `share.created`, `share.revoked`, `placement.added`, `placement.removed`, `content.gc`.

- [ ] **Step 1: Write the failing tests** (append to `server/tests/test_logging_config.py`):

```python
def test_audit_logger_writes_its_own_file(tmp_path, monkeypatch):
    import logging
    from server.logging_config import configure_logging
    from server.audit import audit

    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("LOG_AUDIT_FILE", raising=False)
    configure_logging()
    audit("entry.added", user="u1", doc="d1", via="upload")
    for h in logging.getLogger("server.audit").handlers:
        h.flush()
    line = (tmp_path / "audit.log").read_text()
    assert "entry.added user=u1 doc=d1 via=upload" in line


def test_audit_file_override(tmp_path, monkeypatch):
    import logging
    from server.logging_config import configure_logging
    from server.audit import audit

    target = tmp_path / "elsewhere" / "trail.log"
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_AUDIT_FILE", str(target))
    configure_logging()
    audit("content.gc", doc="d1", trigger="entry_removed")
    for h in logging.getLogger("server.audit").handlers:
        h.flush()
    assert "content.gc doc=d1 trigger=entry_removed" in target.read_text()
```

  Run: `.venv/bin/python -m pytest server/tests/test_logging_config.py -q` → FAIL.

- [ ] **Step 2: Implement.** In `server/logging_config.py`:
  - change the signature to `_dict_config(logfile, level, max_bytes, backups, audit_file)`;
  - add a handler, an `"audit_file"` `RotatingFileHandler` with the same formatter and rotation settings as `"file"` and `"filename": str(audit_file)`;
  - add the logger `"server.audit": {"level": "INFO", "handlers": ["console", "file", "audit_file"], "propagate": False}`. Put it **before** `"server"` in the dict for readability; dictConfig handles either order.

  In `configure_logging()`, add these lines before the `dictConfig` call:

```python
    audit_file = Path(os.environ.get("LOG_AUDIT_FILE") or (log_dir / "audit.log")).resolve()
    audit_file.parent.mkdir(parents=True, exist_ok=True)
```

  Pass `audit_file` through, and add `LOG_AUDIT_FILE` to the module docstring's list of knobs.

  Create `server/audit.py`:

```python
"""Audit trail (A1 spec §10). One line per change to who holds what: entries,
shares, placements, content GC. IDs only — never emails, names, file names or
search text (no personal data at INFO or above). Written to server.log and to
its own rotating file, LOG_AUDIT_FILE (default <LOG_DIR>/audit.log)."""
from __future__ import annotations

import logging

_logger = logging.getLogger("server.audit")


def audit(event: str, **fields: object) -> None:
    _logger.info("%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))
```

- [ ] **Step 3: Run the tests** — `.venv/bin/python -m pytest server/tests/test_logging_config.py -q` → PASS.

- [ ] **Step 4: Commit** (with approval) — `git add server/audit.py server/logging_config.py server/tests/test_logging_config.py && git commit -m "feat(logging): server.audit logger with its own rotating file"`

---

### Task 5: Route every test's document seeding through one helper (current schema)

After this task, Task 6 changes the internals of one file instead of the raw INSERTs in ten test files. Behaviour is unchanged, and the suite stays green.

**Files:**
- Create: `server/tests/seed.py`
- Modify:
  - `server/tests/test_docs_list.py`, `test_docs_patch.py`, `test_docs_authz.py`, `test_doc_grants.py`;
  - `test_library_access_matrix.py`, `test_library_authz.py`, `test_library_schema.py`, `test_auth_authz.py`;
  - `test_projects_router.py`, `test_admin_lifecycle.py`.
- **Do not touch** `test_migration_005.py` or `test_migration_010.py`. They seed *pre-migration* schemas on purpose.

**Interfaces:**
- Produces (used from Task 6 on):
  - `async seed_doc(conn, doc_id, holder_id, *, file_name="f.pdf", file_type="pdf", tags=(), state="registered", project_ids=(), bytes_path=None, extracted_by="client") -> None`
  - `async share_doc(conn, doc_id, sharer_id, recipient_id) -> None`
  - `async place_doc(conn, project_id, doc_id, added_by) -> None`

- [ ] **Step 1: Create `server/tests/seed.py` (current-schema bodies)**

```python
"""The only place tests write documents, entries/shares and project placements.
A schema change to how documents are held touches this file, not every test.

Vocabulary is A1's: a *holder* has the content in their library, a *share*
gives someone else an entry, a *placement* files content into a project.
"""
from __future__ import annotations


async def seed_doc(conn, doc_id, holder_id, *, file_name="f.pdf", file_type="pdf",
                   tags=(), state="registered", project_ids=(), bytes_path=None,
                   extracted_by="client"):
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, tags, "
        "state, pdf_path) VALUES (%s,%s,%s,1,%s,%s,%s,%s)",
        (doc_id, file_name, file_type, holder_id, list(tags), state,
         str(bytes_path) if bytes_path else None))
    for pid in project_ids:
        await place_doc(conn, pid, doc_id, holder_id)


async def share_doc(conn, doc_id, sharer_id, recipient_id):
    await conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)",
        (doc_id, recipient_id))


async def place_doc(conn, project_id, doc_id, added_by):
    await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)",
        (project_id, doc_id))
```

  (`extracted_by` is accepted and ignored until Task 6 adds the column.)

- [ ] **Step 2: Rewire the listed test files.** Run `grep -nE "INSERT INTO (documents|doc_grants|project_documents)" server/tests/*.py`. For every hit outside the two migration tests:
  - where the INSERT sits inside a file-local helper (`_doc`, `_grant`, …), replace the helper's body with the matching `seed.*` call and keep the helper's name and signature;
  - where it is inline in a test, replace it with the `seed.*` call directly.

  Every documents INSERT becomes `seed_doc`, every `doc_grants` INSERT becomes `share_doc` (use the document's owner as `sharer_id`), and every `project_documents` INSERT becomes `place_doc`.

- [ ] **Step 3: Verify nothing changed** — `.venv/bin/python -m pytest server/tests -q`. Expected: the same pass count as before this task.

- [ ] **Step 4: Commit** (with approval) — `git add server/tests && git commit -m "test: route document/share/placement seeding through server/tests/seed.py"`

---

### Task 6: The schema cut: content, entries, placements

This is the largest task: the migration and every route that read the dropped columns move together. Use the most capable model. Two interim behaviours remain until later tasks:
- the JSON `POST /v1/docs` still trusts the client's hash (Task 9 replaces it);
- `/chunks` and `/pdf` stay, gated to upload-entry holders (Task 9 removes them).

**Files:**
- Create: `server/sql/011_content_entries.sql`, `server/tests/test_migration_011.py`
- Create: `server/services/doc_content.py`, `server/tests/test_doc_content.py`
- Modify: `server/auth/authz.py`, `server/routers/docs.py`, `server/routers/projects.py`, `server/routers/admin.py`, `server/tests/seed.py`
- Rename: `server/tests/test_doc_grants.py` → `server/tests/test_doc_shares.py` (rewritten)
- Modify (semantics): the Task 5 test files, as listed in Step 9

**Interfaces:**
- Consumes: `audit()` (Task 4) and `refusal()` (Task 3).
- Produces (`doc_content`):
  - `add_entry(conn, user_id, doc_id, *, via, shared_by=None, file_name=None, tags=None) -> 'created'|'upgraded'|'exists'`
  - `remove_entry(conn, user_id, doc_id) -> bool`
  - `revoke_share(conn, sharer_id, recipient_id, doc_id) -> bool`
  - `holds_entry(conn, user_id, doc_id) -> bool` and `holds_upload(conn, user_id, doc_id) -> bool`
  - `content_ops_refusal(conn, doc_id, user_id, *, is_admin) -> None|'other_holders'|'in_project'`
  - `docs_referenced_by_user(conn, user_id) -> list[str]`
  - `gc_content_if_orphaned(conn, doc_id, *, trigger: str) -> bool`
- Produces (`authz`):
  - `readable_docs_where(alias)` and `readable_docs_params(uid)` (3 params now);
  - `assert_holds_upload(conn, doc_id, user_id)` (404);
  - `can_manage_project_docs(conn, user_id, project_id) -> bool`.
  - **Removed:** `assert_owns_doc`.
- Produces (`seed_doc`): `holder_id=None` allowed (content with no entry).

- [ ] **Step 1: Write the migration test (fails: file missing)**

  Create `server/tests/test_migration_011.py`, modelled on `test_migration_010.py`: create a scratch DB, `apply_migrations(url, max_version=10)`, then seed with raw SQL on the v10 schema:
  - users `alice`, `bob` and `carol`;
  - project `p1`, owned by alice;
  - doc `a` (owner alice, `tags={'x','y'}`, placed in p1, granted to bob, **and** granted to alice herself);
  - doc `b` (owner carol, `state='chunks_uploaded'`, `pdf_path='/tmp/b.pdf'`).

  Run the real `011_content_entries.sql` inside one transaction, then assert:

```python
                cur = await conn.execute(
                    "SELECT user_id, doc_id, added_via, shared_by, tags FROM library_entries")
                entries = {(str(u), d, v, str(s) if s else None, tuple(sorted(t)))
                           for u, d, v, s, t in await cur.fetchall()}
                assert entries == {
                    (str(alice), "a" * 64, "upload", None, ("x", "y")),
                    (str(bob), "a" * 64, "shared", str(alice), ()),
                    (str(carol), "b" * 64, "upload", None, ()),
                }  # alice's self-grant did not add a second row or downgrade hers
                row = await _one(conn, "SELECT added_by FROM project_documents WHERE doc_id=%s", ("a" * 64,))
                assert str(row[0]) == str(alice)
                row = await _one(conn, "SELECT state, bytes_path, extracted_by FROM documents WHERE doc_id=%s", ("b" * 64,))
                assert row == ("extracted", "/tmp/b.pdf", "client")
                for table, col in (("documents", "user_id"), ("documents", "tags"), ("documents", "pdf_path")):
                    assert await _one(conn,
                        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                        (table, col)) is None
                assert await _one(conn, "SELECT to_regclass('doc_grants')") == (None,)
```

- [ ] **Step 2: Write `server/sql/011_content_entries.sql`**

```sql
-- A1: document content vs library entries
-- (docs/superpowers/specs/2026-09-25-document-content-entries-design.md §6).
-- `documents` becomes content owned by nobody; `library_entries` records who
-- holds it; `project_documents` records which projects hold it. One
-- transaction (the runner wraps each file), so there is never a moment with
-- two sources of truth. Back up before upgrading: this drops doc_grants and
-- documents.user_id/tags after copying them.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_by TEXT NOT NULL DEFAULT 'client'
    CHECK (extracted_by IN ('client', 'server'));
ALTER TABLE documents RENAME COLUMN pdf_path TO bytes_path;
UPDATE documents SET state = 'extracted' WHERE state = 'chunks_uploaded';

CREATE TABLE IF NOT EXISTS library_entries (
    user_id    UUID NOT NULL REFERENCES users(id)          ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id)  ON DELETE CASCADE,
    file_name  TEXT,                     -- NULL = the content's canonical name
    tags       TEXT[] NOT NULL DEFAULT '{}',
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_via  TEXT NOT NULL CHECK (added_via IN ('upload', 'shared')),
    shared_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, doc_id)
);
-- PK serves "my library"; this serves "who holds this content" (GC, sole-holder).
CREATE INDEX IF NOT EXISTS library_entries_doc_idx ON library_entries(doc_id);

-- Owners proved possession (they uploaded): upload entries carrying their tags.
INSERT INTO library_entries (user_id, doc_id, tags, added_at, added_via)
SELECT user_id, doc_id, tags, created_at, 'upload' FROM documents
ON CONFLICT DO NOTHING;

-- Grants become shared entries. Only owners could grant (no grantor column),
-- so shared_by = owner loses nothing. A self-grant hits the owner's upload
-- entry and is dropped by ON CONFLICT, never downgrading it.
INSERT INTO library_entries (user_id, doc_id, added_via, shared_by)
SELECT g.grantee_user_id, g.doc_id, 'shared', d.user_id
FROM doc_grants g JOIN documents d ON d.doc_id = g.doc_id
ON CONFLICT DO NOTHING;

-- Placements now carry who filed them: different holders can file the same content.
ALTER TABLE project_documents
    ADD COLUMN IF NOT EXISTS added_by UUID REFERENCES users(id) ON DELETE SET NULL;
UPDATE project_documents pd SET added_by = d.user_id
FROM documents d WHERE d.doc_id = pd.doc_id AND pd.added_by IS NULL;

DROP TABLE IF EXISTS doc_grants;
DROP INDEX IF EXISTS documents_user_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS user_id;
ALTER TABLE documents DROP COLUMN IF EXISTS tags;  -- drops documents_tags_gin with it

INSERT INTO schema_migrations(version) VALUES (11) ON CONFLICT DO NOTHING;
```

  Run: `.venv/bin/python -m pytest server/tests/test_migration_011.py -q` → PASS. The rest of the suite is now red until this task's later steps land.

- [ ] **Step 3: Switch `seed.py` to the new schema**

```python
async def seed_doc(conn, doc_id, holder_id, *, file_name="f.pdf", file_type="pdf",
                   tags=(), state="registered", project_ids=(), bytes_path=None,
                   extracted_by="client"):
    """Content + (unless holder_id is None) the holder's upload entry."""
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
        "bytes_path, extracted_by) VALUES (%s,%s,%s,1,%s,%s,%s)",
        (doc_id, file_name, file_type, state, str(bytes_path) if bytes_path else None,
         extracted_by))
    if holder_id is not None:
        await conn.execute(
            "INSERT INTO library_entries (user_id, doc_id, tags, added_via) "
            "VALUES (%s,%s,%s,'upload')", (holder_id, doc_id, list(tags)))
    for pid in project_ids:
        await place_doc(conn, pid, doc_id, holder_id)


async def share_doc(conn, doc_id, sharer_id, recipient_id):
    await conn.execute(
        "INSERT INTO library_entries (user_id, doc_id, added_via, shared_by) "
        "VALUES (%s,%s,'shared',%s) ON CONFLICT DO NOTHING",
        (recipient_id, doc_id, sharer_id))


async def place_doc(conn, project_id, doc_id, added_by):
    await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id, added_by) VALUES (%s,%s,%s)",
        (project_id, doc_id, added_by))
```

- [ ] **Step 4: Write `server/tests/test_doc_content.py` (fails: module missing)**

```python
from pathlib import Path

from server.services import doc_content
from server.tests.seed import place_doc, seed_doc, share_doc

D = "d" * 64


async def _user(conn, sub):
    from server.auth.users import resolve_or_provision_user
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


async def _project(conn, owner):
    cur = await conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    return str((await cur.fetchone())[0])


async def _via(conn, user, doc=D):
    cur = await conn.execute(
        "SELECT added_via FROM library_entries WHERE user_id=%s AND doc_id=%s", (user, doc))
    row = await cur.fetchone()
    return row[0] if row else None


async def test_add_entry_created_exists_upgraded(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    assert await doc_content.add_entry(db_conn, a, D, via="upload") == "exists"
    assert await doc_content.add_entry(db_conn, b, D, via="shared", shared_by=a) == "created"
    assert await doc_content.add_entry(db_conn, b, D, via="upload") == "upgraded"
    assert await _via(db_conn, b) == "upload"


async def test_share_never_downgrades_an_upload_entry(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    await seed_doc(db_conn, "e" * 64, b)
    await doc_content.add_entry(db_conn, b, D, via="upload")
    assert await doc_content.add_entry(db_conn, b, D, via="shared", shared_by=a) == "exists"
    assert await _via(db_conn, b) == "upload"


async def test_revoke_only_removes_my_share(db_conn):
    a, b, c = (await _user(db_conn, s) for s in "abc")
    await seed_doc(db_conn, D, a)
    await doc_content.add_entry(db_conn, b, D, via="upload")   # b uploaded it too
    await share_doc(db_conn, D, b, c)                          # b shared with c
    assert await doc_content.revoke_share(db_conn, a, c, D) is False  # not a's share
    assert await doc_content.revoke_share(db_conn, a, b, D) is False  # b's upload entry
    assert await doc_content.revoke_share(db_conn, b, c, D) is True


async def test_content_ops_refusal(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    assert await doc_content.content_ops_refusal(db_conn, D, a, is_admin=False) is None
    p = await _project(db_conn, a)
    await place_doc(db_conn, p, D, a)
    assert await doc_content.content_ops_refusal(db_conn, D, a, is_admin=False) == "in_project"
    await share_doc(db_conn, D, a, b)
    assert await doc_content.content_ops_refusal(db_conn, D, a, is_admin=False) == "other_holders"
    assert await doc_content.content_ops_refusal(db_conn, D, b, is_admin=False) == "other_holders"
    assert await doc_content.content_ops_refusal(db_conn, D, b, is_admin=True) is None


async def test_gc_removes_only_orphans_and_their_bytes(db_conn, tmp_path):
    a = await _user(db_conn, "a")
    f = tmp_path / f"{D}.pdf"
    f.write_bytes(b"%PDF-1.4")
    await seed_doc(db_conn, D, a, bytes_path=f)
    await db_conn.execute("INSERT INTO doc_chunks (doc_id, ord, text, text_hash) VALUES (%s,0,'t','h')", (D,))
    assert await doc_content.gc_content_if_orphaned(db_conn, D, trigger="test") is False
    await doc_content.remove_entry(db_conn, a, D)
    assert await doc_content.gc_content_if_orphaned(db_conn, D, trigger="test") is True
    cur = await db_conn.execute("SELECT count(*) FROM doc_chunks WHERE doc_id=%s", (D,))
    assert (await cur.fetchone())[0] == 0 and not f.exists()


async def test_gc_keeps_content_placed_in_a_project(db_conn):
    a = await _user(db_conn, "a")
    await seed_doc(db_conn, D, None, project_ids=[await _project(db_conn, a)])
    assert await doc_content.gc_content_if_orphaned(db_conn, D, trigger="test") is False


async def test_docs_referenced_by_user_includes_owned_project_placements(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    p = await _project(db_conn, a)
    await seed_doc(db_conn, "e" * 64, b, project_ids=[p])
    assert set(await doc_content.docs_referenced_by_user(db_conn, a)) == {D, "e" * 64}
```

- [ ] **Step 5: Implement `server/services/doc_content.py`**

```python
"""Who holds content (A1 spec §3, §5).

Content (`documents`) belongs to nobody. A user holds it through a library
entry; a project holds it through a placement. This module owns the writes
that change who holds content, the sole-holder rule for content-changing ops,
and garbage collection. Routes never DELETE FROM documents themselves.

GC rule (spec §3): every path that removes entries or placements collects the
affected doc_ids FIRST and calls gc_content_if_orphaned for each AFTERWARDS —
an SQL ON DELETE CASCADE never runs this code.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..audit import audit

logger = logging.getLogger(__name__)


async def add_entry(conn, user_id, doc_id, *, via, shared_by=None, file_name=None, tags=None) -> str:
    """Give `user_id` an entry for `doc_id`. 'created', 'upgraded' (a shared
    entry became an upload entry — the user just proved possession) or
    'exists'. An upload entry is never downgraded to shared (spec §5)."""
    cur = await conn.execute(
        "INSERT INTO library_entries (user_id, doc_id, file_name, tags, added_via, shared_by) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, doc_id) DO NOTHING RETURNING 1",
        (user_id, doc_id, file_name, sorted(set(tags or [])), via, shared_by))
    if await cur.fetchone():
        return "created"
    if via != "upload":
        return "exists"
    cur = await conn.execute(
        "UPDATE library_entries SET added_via = 'upload', shared_by = NULL "
        "WHERE user_id = %s AND doc_id = %s AND added_via = 'shared'", (user_id, doc_id))
    if tags:
        await conn.execute(
            "UPDATE library_entries SET tags = %s WHERE user_id = %s AND doc_id = %s",
            (sorted(set(tags)), user_id, doc_id))
    return "upgraded" if cur.rowcount == 1 else "exists"


async def remove_entry(conn, user_id, doc_id) -> bool:
    cur = await conn.execute(
        "DELETE FROM library_entries WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
    return cur.rowcount == 1


async def revoke_share(conn, sharer_id, recipient_id, doc_id) -> bool:
    """Removes only a shared entry this sharer created (spec §5)."""
    cur = await conn.execute(
        "DELETE FROM library_entries WHERE user_id = %s AND doc_id = %s "
        "AND added_via = 'shared' AND shared_by = %s", (recipient_id, doc_id, sharer_id))
    return cur.rowcount == 1


async def holds_entry(conn, user_id, doc_id) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
    return await cur.fetchone() is not None


async def holds_upload(conn, user_id, doc_id) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE user_id = %s AND doc_id = %s "
        "AND added_via = 'upload'", (user_id, doc_id))
    return await cur.fetchone() is not None


async def content_ops_refusal(conn, doc_id, user_id, *, is_admin) -> str | None:
    """None when `user_id` may change the content itself (convert, delete
    converted markdown, re-index): an admin, or the sole holder — exactly one
    entry, theirs, and no placements. Otherwise the reason for the 409."""
    if is_admin:
        return None
    cur = await conn.execute(
        "SELECT count(*) FILTER (WHERE user_id <> %s), count(*) FILTER (WHERE user_id = %s) "
        "FROM library_entries WHERE doc_id = %s", (user_id, user_id, doc_id))
    others, mine = await cur.fetchone()
    if others or not mine:
        return "other_holders"
    cur = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM project_documents WHERE doc_id = %s)", (doc_id,))
    return "in_project" if (await cur.fetchone())[0] else None


async def docs_referenced_by_user(conn, user_id) -> list[str]:
    """Docs whose last reference may vanish when this user is deleted: their
    entries, plus placements in projects they own (projects cascade on user
    delete until A0 ends that)."""
    cur = await conn.execute(
        "SELECT doc_id FROM library_entries WHERE user_id = %s "
        "UNION SELECT pd.doc_id FROM project_documents pd "
        "JOIN projects p ON p.id = pd.project_id WHERE p.owner_user_id = %s",
        (user_id, user_id))
    return [r[0] for r in await cur.fetchall()]


async def gc_content_if_orphaned(conn, doc_id, *, trigger: str) -> bool:
    """Delete content nobody holds: row (cascading chunks and pages) and bytes.
    FOR UPDATE on the row blocks a concurrent entry insert (its FK check takes
    KEY SHARE) until we commit. The bytes file is unlinked while that lock is
    held, before commit; uploads move their file into place only after their
    own INSERT succeeds, so we can never delete a newer upload's file."""
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT bytes_path FROM documents WHERE doc_id = %s FOR UPDATE", (doc_id,))
        row = await cur.fetchone()
        if row is None:
            return False
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM library_entries WHERE doc_id = %s) "
            "OR EXISTS (SELECT 1 FROM project_documents WHERE doc_id = %s)", (doc_id, doc_id))
        if (await cur.fetchone())[0]:
            return False
        if row[0]:
            try:
                Path(row[0]).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not remove bytes for doc %s: %s", doc_id, e)
        await conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
    audit("content.gc", doc=doc_id, trigger=trigger)
    return True
```

  Run: `.venv/bin/python -m pytest server/tests/test_doc_content.py -q` → PASS.

- [ ] **Step 6: Rewrite `server/auth/authz.py`'s document parts**

  Remove `assert_owns_doc`. Keep `_owner` and `assert_owns_session`. Replace `readable_docs_where` / `readable_docs_params` and add the two new functions:

```python
def readable_docs_where(alias: str = "d") -> str:
    """The ONE definition of "can read this document" (A1 spec §3): the user
    has a library entry for it, or it is placed in a project the user owns or
    belongs to. `<alias>` is a `documents` row. Bind with
    `readable_docs_params(user_id)`. Subquery aliases are underscore-prefixed
    so they can't shadow the caller's. Resolved in SQL, never in Python."""
    return (
        f"(EXISTS (SELECT 1 FROM library_entries _re "
        f"WHERE _re.doc_id = {alias}.doc_id AND _re.user_id = %s) "
        f"OR EXISTS (SELECT 1 FROM project_documents _rpd "
        f"JOIN projects _rp ON _rp.id = _rpd.project_id "
        f"WHERE _rpd.doc_id = {alias}.doc_id AND (_rp.owner_user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members _rpm "
        f"WHERE _rpm.project_id = _rp.id AND _rpm.user_id = %s))))"
    )


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id, user_id]


async def assert_holds_upload(conn, doc_id: str, user_id: str) -> None:
    """404 unless the user holds an UPLOAD entry — they proved possession of
    the bytes, which is what sharing and filing into a project require."""
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE doc_id = %s AND user_id = %s "
        "AND added_via = 'upload'", (doc_id, user_id))
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Document not found")


async def can_manage_project_docs(conn, user_id: str, project_id) -> bool:
    """The one seam for "may remove a document from this project" (A1 §5).
    A1: the project owner. A0 swaps the body to "role >= maintainer"."""
    cur = await conn.execute(
        "SELECT 1 FROM projects WHERE id = %s AND owner_user_id = %s", (project_id, user_id))
    return await cur.fetchone() is not None
```

  Update the module docstring: it covers read predicates and ownership guards, not only "row ownership".

- [ ] **Step 7: Move `server/routers/docs.py` onto entries**

  Apply each change below. Keep every other route as is for now.

  (a) Imports: drop `assert_owns_doc`. Add `assert_holds_upload`, `from ..services import doc_content`, `from ..audit import audit` and `from ..http_errors import refusal`.

  (b) Project links: visible projects only (spec §3; C0's doc-owner exception is gone):

```python
def _doc_projects_sql(alias: str) -> str:
    """JSON array `[{id, name}]` of the projects `<alias>` is placed in that the
    caller can see. Everyone — including whoever uploaded it — sees only
    projects they own or belong to: listing others would leak their names, and
    under A1 an uploader can't unlink from a project anyway. Sorted by name,
    then id. Bind with `_doc_projects_params(user_id)`; SELECT-list params come
    BEFORE join and WHERE params."""
    return (
        "COALESCE((SELECT json_agg(json_build_object('id', _vp.id, 'name', _vp.name) "
        "ORDER BY _vp.name, _vp.id) "
        "FROM project_documents _vpd JOIN projects _vp ON _vp.id = _vpd.project_id "
        f"WHERE _vpd.doc_id = {alias}.doc_id AND {visible_projects_where('_vp')}), '[]'::json)"
    )


def _doc_projects_params(user_id: str) -> list[str]:
    return visible_projects_params(user_id)


# The caller's own entry (if any) and who shared it. Bind `_ENTRY_JOIN` with
# [user_id], after SELECT-list params and before WHERE params.
_ENTRY_JOIN = (
    "LEFT JOIN library_entries _me ON _me.doc_id = d.doc_id AND _me.user_id = %s "
    "LEFT JOIN users _sb ON _sb.id = _me.shared_by"
)
_ENTRY_COLS = (
    "COALESCE(_me.file_name, d.file_name) AS file_name, "
    "COALESCE(_me.tags, '{}') AS tags, _me.added_via AS added_via, "
    "_me.shared_by AS shared_by_id, COALESCE(_sb.display_name, _sb.email) AS shared_by_name"
)


def _entry_fields(rec: dict[str, Any]) -> dict[str, Any]:
    sid, sname = rec.pop("shared_by_id", None), rec.pop("shared_by_name", None)
    rec["shared_by"] = {"id": str(sid), "name": sname} if sid else None
    rec["in_library"] = rec.get("added_via") is not None
    return rec
```

  (c) `_fetch_doc_status`: select `{_ENTRY_COLS}` in place of `d.file_name` / `d.tags`, and `d.bytes_path` in place of `d.pdf_path`. Add `{_ENTRY_JOIN}` right after `FROM documents d`. Bind `[*_doc_projects_params(user_id), user_id, doc_id]`. After building `rec`:

```python
    rec["has_pdf"] = bool(rec.pop("bytes_path", None)) and rec["file_type"] == "pdf"
    return _entry_fields(rec)
```

  (d) `list_documents`:

```python
    where = [readable_docs_where("d")]
    where_params: list[Any] = readable_docs_params(uid)
    if q:
        where.append("(COALESCE(_me.file_name, d.file_name) ILIKE %s OR %s = ANY(_me.tags))")
        where_params += [f"%{q}%", q]
    if project_id:
        ...  # unchanged EXISTS over visible projects
    if tag:
        where.append("%s = ANY(_me.tags)")
        where_params.append(tag)
    sql = (
        f"SELECT d.doc_id, d.state, {_ENTRY_COLS}, {_doc_projects_sql('d')} AS projects "
        f"FROM documents d {_ENTRY_JOIN} "
        f"WHERE {' AND '.join(where)} ORDER BY d.updated_at DESC"
    )
    params = [*_doc_projects_params(uid), uid, *where_params]
    ...
        cur = await conn.execute(sql, params)
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in await cur.fetchall()]
    return [_entry_fields(r) for r in rows]
```

  Update the docstring: "docs in my library (uploaded or shared with me) plus docs placed in projects I own or belong to".

  (e) Gates: rename `_require_doc_owner` → `_require_upload_holder`. Its body calls `assert_holds_upload(conn, doc_id, principal.user_id)`. Use it on `/chunks`, `/index`, `/pdf` (both), `/convert` and `DELETE /markdown`; those routes change again in Tasks 8–10.

  (f) Interim `register_document` (JSON). Remove the 404-if-foreign check. Keep the call to `_fetch_doc_status`.

```python
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, page_count) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (doc_id) DO NOTHING",
            (payload.doc_id, payload.file_name, payload.file_type, payload.size_bytes,
             payload.page_count))
        if await doc_content.add_entry(conn, principal.user_id, payload.doc_id, via="upload") != "exists":
            audit("entry.added", user=principal.user_id, doc=payload.doc_id, via="upload")
        status = await _fetch_doc_status(conn, payload.doc_id, principal.user_id)
```

  (g) `upload_chunks`: advance the state to `'extracted'` (not `'chunks_uploaded'`). `upload_pdf_bytes`, `delete_pdf_bytes` and `_run_convert_job` / `start_convert_job`: rename the `pdf_path` column to `bytes_path` in their SQL.

  (h) `PATCH /{doc_id}` edits **my entry** only:

```python
class DocPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_name: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None
```

```python
    data = body.model_dump(exclude_unset=True)
    async with pool.connection() as conn:
        if not await doc_content.holds_entry(conn, principal.user_id, doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
        sets, params = [], []
        if "tags" in data:
            sets.append("tags = %s"); params.append(sorted(set(data["tags"] or [])))
        if "file_name" in data:
            # "" or null resets to the content's canonical name
            sets.append("file_name = %s"); params.append((data["file_name"] or "").strip() or None)
        if sets:
            await conn.execute(
                f"UPDATE library_entries SET {', '.join(sets)} WHERE user_id = %s AND doc_id = %s",
                [*params, principal.user_id, doc_id])
        status = await _fetch_doc_status(conn, doc_id, principal.user_id)
```

  Update the docstring: `owner_user_id` is gone (422 via `extra="forbid"`), and reassignment no longer exists.

  (i) `DELETE /{doc_id}` removes my entry, then GCs:

```python
@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: DocId, principal: Principal = Depends(require_capability("reader"))
):
    """Remove the document from MY library. Other people's entries and project
    placements are untouched; the content itself goes only when nobody holds
    it any more (spec §3 GC)."""
    _ensure_ready()
    async with get_pool().connection() as conn:
        if not await doc_content.remove_entry(conn, principal.user_id, doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
        audit("entry.removed", user=principal.user_id, doc=doc_id)
        if await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="entry_removed"):
            _doc_job_locks.pop(doc_id, None)
    return Response(status_code=204)
```

  (j) Replace both `/grants/{user_id}` routes with `/shares/{user_id}`:

```python
@router.put("/{doc_id}/shares/{user_id}", status_code=204)
async def add_share(doc_id: DocId, user_id: str,
                    principal: Principal = Depends(_require_upload_holder)):
    """Share with one user: creates their `shared` entry. Only an upload-entry
    holder may share (they proved possession); recipients can't re-share. A
    recipient who already holds the content keeps their entry unchanged."""
    _ensure_ready()
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        async with get_pool().connection() as conn:
            async with conn.transaction():  # savepoint: a caught FK error leaves conn usable
                outcome = await doc_content.add_entry(
                    conn, user_id, doc_id, via="shared", shared_by=principal.user_id)
    except pg_errors.ForeignKeyViolation:
        raise HTTPException(status_code=404, detail="User not found")
    if outcome == "created":
        audit("share.created", by=principal.user_id, to=user_id, doc=doc_id)
    return Response(status_code=204)


@router.delete("/{doc_id}/shares/{user_id}", status_code=204)
async def remove_share(doc_id: DocId, user_id: str,
                       principal: Principal = Depends(require_capability("reader"))):
    """Revoke a share I created. Never removes someone's own upload or another
    sharer's share (404). Recipients remove their own copy via DELETE /{id}."""
    _ensure_ready()
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    async with get_pool().connection() as conn:
        if not await doc_content.revoke_share(conn, principal.user_id, user_id, doc_id):
            raise HTTPException(status_code=404, detail="Share not found")
        audit("share.revoked", by=principal.user_id, to=user_id, doc=doc_id)
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="share_revoked")
    return Response(status_code=204)
```

  Update the module docstring's state list: `stored → extracting → extracted → indexing → indexed | failed`. `registered` is legacy only.

- [ ] **Step 8: Move `server/routers/projects.py` link/unlink onto entries, and `admin.py` user deletion onto GC**

  `projects.py`: import `assert_holds_upload` and `can_manage_project_docs`, plus `doc_content` and `audit`. Replace the two doc routes' bodies:

```python
    # link_doc — after the project-visibility check (unchanged; admins skip it):
    await assert_holds_upload(conn, doc_id, principal.user_id)
    cur = await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id, added_by) VALUES (%s,%s,%s) "
        "ON CONFLICT DO NOTHING", (project_id, doc_id, principal.user_id))
    if cur.rowcount:
        audit("placement.added", project=project_id, doc=doc_id, by=principal.user_id)
    return Response(status_code=204)
```

```python
@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def unlink_doc(project_id: uuid.UUID, doc_id: DocId,
                     principal: deps.Principal = Depends(deps.require_capability("reader")),
                     conn=Depends(deps.get_conn)):
    """Remove a doc from a project. Projects govern their documents (A1 §5):
    only `can_manage_project_docs` (A1: the project owner) may — the uploader
    has no special power. Everyone else, and a missing link, gets 404."""
    if not await can_manage_project_docs(conn, principal.user_id, project_id):
        raise HTTPException(status_code=404, detail="Not found")
    cur = await conn.execute(
        "DELETE FROM project_documents WHERE project_id = %s AND doc_id = %s",
        (project_id, doc_id))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Not found")
    audit("placement.removed", project=project_id, doc=doc_id, by=principal.user_id)
    await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="placement_removed")
    return Response(status_code=204)
```

  Update the module docstring: holders of an upload entry file documents in; only the project removes them.

  `admin.py` `delete_user`: replace the `pdf_path` pre-collection and the unlink loop:

```python
    # Collect every doc this deletion can orphan BEFORE the rows cascade away:
    # the user's entries, and placements in projects they own (A1 §3). GC runs
    # after — a cascade never runs app code.
    doc_ids = await doc_content.docs_referenced_by_user(conn, user_id)
    await users.delete_user(conn, user_id)
    ...  # Keycloak delete unchanged
    for doc_id in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="user_deleted")
    return Response(status_code=204)
```

  Remove the now-unused `Path` import if nothing else uses it.

- [ ] **Step 9: Bring the existing tests to the new semantics**

  Run `.venv/bin/python -m pytest server/tests -q` and fix each failure by updating the **test** to the spec's behaviour, as listed below. Never bend the code back to old behaviour.
  - `test_doc_grants.py` → `git mv` to `test_doc_shares.py`, and rewrite it against `/shares`:
    - an upload holder shares → the recipient can read;
    - a recipient can't re-share (404);
    - a non-holder can't share (404);
    - sharing with an unknown or invalid user → 404;
    - sharing with someone who holds an upload entry leaves it `upload`;
    - the sharer revokes → the recipient loses access (404 on GET);
    - revoking someone else's share → 404;
    - revoking an upload entry → 404.
  - `test_docs_list.py`: rows no longer carry `owner_user_id` / `is_owner`. Assert:
    - own row: `in_library is True`, `added_via == "upload"`, `shared_by is None`;
    - shared row: `added_via == "shared"` and `shared_by["id"] == sharer`;
    - project-only row: `in_library is False`, `added_via is None`.

    Tags and `q` filters use entry tags.
  - `test_docs_patch.py`:
    - delete the admin-reassignment tests;
    - add: `owner_user_id` → 422;
    - add: renaming my entry changes only my row's `file_name` (another holder still sees the canonical name);
    - add: `file_name: ""` resets to canonical;
    - add: patching a doc I only see via a project → 404.
  - `test_projects_router.py`: invert every C0 test where the doc owner unlinks from someone else's project, or sees a link to a project they left. Both are now 404 / not listed. Add: a project owner unlinks the last reference → the content is GC'd.
  - `test_admin_lifecycle.py::test_delete_sweeps_users_pdf_files` → rename to `test_delete_user_gcs_sole_held_content`. The deleted user's sole-held doc: row and file gone. A doc they held that another user also holds: row and file kept. A doc placed only in a project the deleted user owned: row gone.
  - `test_library_access_matrix.py`, `test_library_authz.py`, `test_auth_authz.py`, `test_docs_authz.py`, `test_library_schema.py`:
    - "owner" means holding an upload entry, and "grantee" means holding a shared entry;
    - schema assertions check `library_entries` and no longer check `doc_grants` or `documents.tags`;
    - add a delete test: a recipient deleting their entry leaves the sharer's intact.

- [ ] **Step 10: Run everything** — `.venv/bin/python -m pytest server/tests -q` → all pass. Then `grep -rn "doc_grants\|assert_owns_doc\|pdf_path\|chunks_uploaded\|owner_user_id" server --include='*.py'`. The only allowed hits are the migration tests, `projects.owner_user_id` (A0 renames it), and `test_migration_011.py`.

- [ ] **Step 11: Commit** (with approval) — `git add -A server && git commit -m "feat(library): content vs library entries — migration 011, entry-based access, shares, GC"`

---

### Task 7: GC on project deletion, startup sweep, startup state recovery

**Files:**
- Modify: `server/services/doc_content.py`, `server/routers/projects.py`, `server/db.py`
- Test: `server/tests/test_doc_content.py`, `server/tests/test_projects_router.py`

**Interfaces:**
- Produces: `sweep_orphans(conn) -> int` and `recover_states(conn) -> None`.

- [ ] **Step 1: Failing tests** (append to `test_doc_content.py`):

```python
async def test_sweep_removes_orphans_only(db_conn):
    a = await _user(db_conn, "a")
    await seed_doc(db_conn, D, a)
    await seed_doc(db_conn, "e" * 64, None)  # planted orphan
    assert await doc_content.sweep_orphans(db_conn) >= 1
    cur = await db_conn.execute("SELECT doc_id FROM documents WHERE doc_id IN (%s,%s)", (D, "e" * 64))
    assert [r[0] for r in await cur.fetchall()] == [D]


async def test_recover_states(db_conn):
    a = await _user(db_conn, "a")
    await seed_doc(db_conn, D, a, state="extracting")
    await seed_doc(db_conn, "e" * 64, a, state="indexing")
    await doc_content.recover_states(db_conn)
    cur = await db_conn.execute("SELECT doc_id, state FROM documents WHERE doc_id IN (%s,%s)", (D, "e" * 64))
    assert dict(await cur.fetchall()) == {D: "stored", "e" * 64: "extracted"}
```

  In `test_projects_router.py`, add: deleting a project whose docs have no other holder removes those docs; a doc also held in someone's library survives.

- [ ] **Step 2: Implement** (append to `doc_content.py`):

```python
async def sweep_orphans(conn) -> int:
    """Startup safety net (spec §3): GC any content with no entry and no
    placement — catches a future code path that removed references without
    calling gc_content_if_orphaned."""
    cur = await conn.execute(
        "SELECT d.doc_id FROM documents d "
        "WHERE NOT EXISTS (SELECT 1 FROM library_entries e WHERE e.doc_id = d.doc_id) "
        "AND NOT EXISTS (SELECT 1 FROM project_documents pd WHERE pd.doc_id = d.doc_id)")
    removed = 0
    for (doc_id,) in await cur.fetchall():
        if await gc_content_if_orphaned(conn, doc_id, trigger="startup_sweep"):
            removed += 1
    if removed:
        logger.warning("Startup sweep removed %d orphaned documents", removed)
    return removed


async def recover_states(conn) -> None:
    """A crash mid-job leaves docs mid-state; make them resumable (spec §4)."""
    await conn.execute(
        "UPDATE documents SET state = 'stored', updated_at = now() WHERE state = 'extracting'")
    await conn.execute(
        "UPDATE documents SET state = 'extracted', updated_at = now() WHERE state = 'indexing'")
```

  `projects.py` `delete_project`: after `_assert_owner`, collect the placements and GC them after the delete:

```python
    cur = await conn.execute(
        "SELECT doc_id FROM project_documents WHERE project_id = %s", (project_id,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    for doc_id in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="project_deleted")
```

  `db.py`: replace the `UPDATE documents SET state = 'chunks_uploaded' WHERE state = 'indexing'` statement with:

```python
                # A crash mid-job leaves docs mid-state: make them resumable,
                # then GC any content nothing references (A1 spec §3, §4).
                await doc_content.recover_states(conn)
                await doc_content.sweep_orphans(conn)
```

  Add `from .services import doc_content` at the top. Keep the `conversion_state` recovery below it unchanged.

- [ ] **Step 3: Run** `.venv/bin/python -m pytest server/tests -q` → PASS.

- [ ] **Step 4: Commit** (with approval) — `git commit -am "feat(library): GC on project delete, startup orphan sweep and state recovery"`

---

### Task 8: Background pipeline: extract from verified bytes → embed; legacy swap; resume vs re-index

**Files:**
- Create: `server/services/doc_pipeline.py`, `server/tests/docs_harness.py`, `server/tests/test_doc_pipeline.py`
- Modify: `server/routers/docs.py`

**Interfaces:**
- Consumes:
  - `extract.extract_file` (Task 2);
  - `doc_content.content_ops_refusal` and `holds_entry` (Task 6);
  - `refusal` (Task 3).
- Produces (`doc_pipeline`):
  - `doc_lock(doc_id) -> asyncio.Lock` and `text_hash(text) -> str`;
  - `async replace_chunks(conn, doc_id, chunks, *, embeddings=None, model=None)` and `async chunks_from_pages(doc_id) -> int`;
  - `async run_embed(doc_id)`, `async run_pipeline(doc_id)` and `async run_legacy_swap(doc_id)`.
- Produces (tests): `build_docs_app(db_conn, monkeypatch, *, storage_dir, embed=None) -> (app, as_user)`, where `as_user(principal)` sets the caller.
- Produces (`docs.py`): `_content_shared(reason, doc_id, user_id) -> HTTPException`.

- [ ] **Step 1: Create the test harness `server/tests/docs_harness.py`**

```python
"""App + pool shim for doc-route tests that exercise background jobs.

docs.py and doc_pipeline.py reach the DB through their own imported
`get_pool`; both are patched to hand back the test's transactional conn.
httpx's ASGITransport awaits the whole ASGI call, so FastAPI background tasks
have finished by the time a request returns — pipeline tests are deterministic.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import docs as docs_router
from server.services import doc_pipeline
from server.services.embeddings import EMBEDDING_DIM


class _PoolShim:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


async def fake_embed(texts):
    return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]


def build_docs_app(db_conn, monkeypatch, *, storage_dir, embed=None):
    shim = _PoolShim(db_conn)
    for mod in (docs_router, doc_pipeline):
        monkeypatch.setattr(mod, "get_pool", lambda: shim)
        monkeypatch.setattr(mod, "is_ready", lambda: True)
    monkeypatch.setattr(doc_pipeline, "embed_batch", embed or fake_embed)
    monkeypatch.setenv("DOC_STORAGE_DIR", str(storage_dir))
    current = {}
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    app.include_router(docs_router.router)

    def as_user(principal):
        current["p"] = principal
        return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    return app, as_user
```

- [ ] **Step 2: Failing tests** in `server/tests/test_doc_pipeline.py`. Use `seed.seed_doc` with real bytes written into `tmp_path / "store"` (the file's name is `<doc_id>.<ext>`, and it's passed as `bytes_path`), and `make_pdf` from Task 2. Principals come from `resolve_or_provision_user`, with `role="member"` and `capabilities=frozenset({"reader"})`. Cover these cases:
  - `run_pipeline` on a `stored` text doc → `state='indexed'`, `extracted_by='server'`, chunks equal to `extract.extract_file(...)` in `(ord, page, chunk_type, text)`, `page_count` set, and every embedding non-NULL;
  - `run_pipeline` on a `stored` doc whose bytes are **a corrupt PDF** (`b"%PDF-1.4 garbage"`) → `state='failed'` with a non-empty `error_message`; no exception escapes; the entry is still present. *(Review Focus 2)*
  - `run_pipeline` on a `stored` doc with `bytes_path=None` → `failed`, with `error_message` containing "upload the file again";
  - `run_pipeline` on a doc with `conversion_state='converted'` and two `doc_pages` rows → chunks are `page-md` from the pages, not native;
  - `run_legacy_swap` on an `indexed`, `extracted_by='client'` doc with one old chunk "OLD" (embedded) and real bytes → the old chunk is gone, new chunks are embedded, `extracted_by='server'`, `conversion_state` is NULL and `doc_pages` is empty;
  - `run_legacy_swap` when the fake embed raises → the old chunk "OLD" is still present with its embedding, and `extracted_by` is still `'client'`;
  - `POST /v1/docs/{id}/index`:
    - on a `failed` doc by a shared-entry holder → 202, and after the call the doc is `indexed` (resume);
    - on an `indexed` doc by that same shared holder → 409 with `detail.error == "content_shared"` and `detail.reason == "other_holders"`;
    - on an `indexed` doc by the sole holder → 202, then `indexed` again;
    - by a project-only reader on a `failed` doc → 404.

  Run: `.venv/bin/python -m pytest server/tests/test_doc_pipeline.py -q` → FAIL.

- [ ] **Step 3: Create `server/services/doc_pipeline.py`**

  Move these from `docs.py` into it, adjusting the names:
  - `_doc_job_locks` + `_get_doc_lock` → `doc_lock`;
  - `_text_hash` → `text_hash`;
  - `_run_index_job` → `run_embed`, **verbatim body**;
  - `_chunks_from_pages` → `chunks_from_pages`, verbatim.

  In `docs.py`, import them and keep the old private names as aliases (`_doc_job_locks = doc_pipeline._doc_job_locks`, `_get_doc_lock = doc_pipeline.doc_lock`, `_run_index_job = doc_pipeline.run_embed`, `_chunks_from_pages = doc_pipeline.chunks_from_pages`), so the convert job and existing tests still resolve. Then add:

```python
"""Background document jobs (A1 spec §4): extract text from verified bytes,
embed chunks, and the one-time re-extraction of legacy browser-indexed content.

One asyncio lock per doc serializes every job that touches a doc's chunks.
The lock is per process: two server workers may duplicate work on the same
new hash, and converge on UNIQUE(doc_id, text_hash) — wasted work, not wrong
data (spec §4)."""
# (imports: asyncio, hashlib, logging, Path; from ..db import get_pool, is_ready;
#  from . import extract, model_router; from .embeddings import EMBEDDING_DIM, embed_batch)

EMBED_BATCH = 16


async def replace_chunks(conn, doc_id, chunks, *, embeddings=None, model=None) -> None:
    """Swap a doc's whole chunk set in ONE transaction: readers see the old set
    until commit and never a half-swapped index (spec §4, legacy content)."""
    rows = [
        (doc_id, c.ord, c.page, c.chunk_type, c.text, text_hash(c.text),
         embeddings[i] if embeddings else None, model if embeddings else None)
        for i, c in enumerate(chunks)
    ]
    async with conn.transaction():
        await conn.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, "
                "embedding, embedding_model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (doc_id, text_hash) DO UPDATE SET ord = EXCLUDED.ord, "
                "page = EXCLUDED.page, chunk_type = EXCLUDED.chunk_type, "
                "embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model",
                rows)


async def _fail(doc_id: str, message: str) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE documents SET state = 'failed', error_message = %s, updated_at = now() "
            "WHERE doc_id = %s", (message[:500], doc_id))


async def run_pipeline(doc_id: str) -> None:
    """stored | extracting | failed → extract (native, or from converted pages)
    → extracted → embed → indexed. `extracted` skips straight to embedding.
    Every failure lands in state='failed' with a readable error_message."""
    async with doc_lock(doc_id):
        if not is_ready():
            logger.warning("Skipping pipeline for %s — DB not ready", doc_id)
            return
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "SELECT state, file_type, bytes_path, conversion_state FROM documents "
                "WHERE doc_id = %s", (doc_id,))
            row = await cur.fetchone()
        if row is None:
            return
        state, file_type, bytes_path, conversion_state = row
        if state in ("stored", "extracting", "failed"):
            # Only native extraction from verified bytes makes content
            # server-derived; re-seeding from converted pages leaves
            # extracted_by as it was (legacy conversions stay 'client').
            native = conversion_state != "converted"
            try:
                if not native:
                    await chunks_from_pages(doc_id)
                    page_count = None
                else:
                    if not bytes_path or not Path(bytes_path).is_file():
                        raise RuntimeError("No stored file — upload the file again.")
                    async with get_pool().connection() as conn:
                        await conn.execute(
                            "UPDATE documents SET state = 'extracting', error_message = NULL, "
                            "updated_at = now() WHERE doc_id = %s", (doc_id,))
                    result = await asyncio.to_thread(extract.extract_file, Path(bytes_path), file_type)
                    async with get_pool().connection() as conn:
                        await replace_chunks(conn, doc_id, result.chunks)
                    page_count = result.page_count
                    logger.debug("Extracted %d chunks from %d pages for %s",
                                 len(result.chunks), page_count, doc_id)
                async with get_pool().connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET state = 'extracted', "
                        "extracted_by = CASE WHEN %s THEN 'server' ELSE extracted_by END, "
                        "page_count = COALESCE(%s, page_count), updated_at = now() "
                        "WHERE doc_id = %s", (native, page_count, doc_id))
            except Exception as e:
                logger.error("Extraction failed for %s: %s", doc_id, e)
                await _fail(doc_id, f"Could not extract text: {e}")
                return
        elif state != "extracted":
            return  # indexing / indexed: nothing to do
        async with get_pool().connection() as conn:
            await conn.execute(
                "UPDATE documents SET state = 'indexing', updated_at = now() WHERE doc_id = %s",
                (doc_id,))
    await run_embed(doc_id)  # takes the same lock itself


async def run_legacy_swap(doc_id: str) -> None:
    """Content indexed from browser chunks before A1 is re-extracted ONCE from
    the first verified upload (spec §4). New chunks are extracted and embedded
    first; one transaction then swaps them in. Any failure keeps the old
    index untouched and leaves extracted_by='client' so a later upload retries.
    A conversion made from the old, unverified bytes is discarded with it."""
    embed_model = model_router.get_config().embed_model
    async with doc_lock(doc_id):
        if not is_ready():
            return
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "SELECT state, extracted_by, bytes_path, file_type FROM documents "
                "WHERE doc_id = %s", (doc_id,))
            row = await cur.fetchone()
        if not row or row[0] != "indexed" or row[1] != "client" or not row[2]:
            return
        try:
            result = await asyncio.to_thread(extract.extract_file, Path(row[2]), row[3])
            texts = [c.text for c in result.chunks]
            vectors = []
            for i in range(0, len(texts), EMBED_BATCH):
                vectors += await embed_batch(texts[i:i + EMBED_BATCH])
            if any(v is None for v in vectors):
                raise RuntimeError("embedding service skipped some chunks")
        except Exception as e:
            logger.error("Legacy re-extraction failed for %s; keeping the old index: %s", doc_id, e)
            return
        async with get_pool().connection() as conn:
            async with conn.transaction():
                await replace_chunks(conn, doc_id, result.chunks, embeddings=vectors, model=embed_model)
                await conn.execute("DELETE FROM doc_pages WHERE doc_id = %s", (doc_id,))
                await conn.execute(
                    "UPDATE documents SET extracted_by = 'server', state = 'indexed', "
                    "page_count = %s, embedding_model = %s, embedding_dim = %s, "
                    "conversion_state = NULL, conversion_options = NULL, conversion_error = NULL, "
                    "converted_at = NULL, error_message = NULL, updated_at = now() "
                    "WHERE doc_id = %s",
                    (result.page_count, embed_model, EMBEDDING_DIM, doc_id))
        logger.warning("Legacy content %s re-extracted from verified bytes", doc_id)
```

- [ ] **Step 4: Rewrite `POST /{doc_id}/index` in `docs.py`**

```python
_CONTENT_SHARED_MESSAGES = {
    "other_holders": "Other people also use this document, so it can't be changed here. Ask an admin.",
    "in_project": ("This document is in a project, so changing it would change it for the project "
                   "too. Remove it from the project first, or ask an admin."),
}


def _content_shared(reason: str, doc_id: str, user_id: str) -> HTTPException:
    logger.warning("content_shared refusal: user %s doc %s reason %s", user_id, doc_id, reason)
    return refusal(409, "content_shared", _CONTENT_SHARED_MESSAGES[reason], reason=reason)


@router.post("/{doc_id}/index", status_code=202)
async def start_index_job(doc_id: DocId, background: BackgroundTasks,
                          principal: Principal = Depends(_require_doc_reader)) -> dict[str, Any]:
    """Resume or re-index (spec §4). On content that isn't `indexed`, any entry
    holder may RESUME it (a crash, a failure). On `indexed` content this is a
    RE-INDEX from the stored bytes — a content-changing op, so only the sole
    holder or an admin (else 409 content_shared)."""
    _ensure_ready()
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
        state = (await cur.fetchone())[0]
        if state == "indexed":
            reason = await doc_content.content_ops_refusal(
                conn, doc_id, principal.user_id, is_admin=principal.role == "admin")
            if reason:
                raise _content_shared(reason, doc_id, principal.user_id)
        elif not await doc_content.holds_entry(conn, principal.user_id, doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
        if state in ("extracting", "indexing"):
            return {"ok": True, "doc_id": doc_id, "state": state}  # already running
        if state in ("indexed", "failed", "registered"):
            await conn.execute(
                "UPDATE documents SET state = 'stored', error_message = NULL, updated_at = now() "
                "WHERE doc_id = %s", (doc_id,))
    background.add_task(doc_pipeline.run_pipeline, doc_id)
    return {"ok": True, "doc_id": doc_id, "state": "extracting"}
```

  In `_run_convert_job`: when conversion fails, it must not leave chunks half-built. The existing behaviour already covers that. Its `bytes_path` reading is unchanged from Task 6.

- [ ] **Step 5: Run** `.venv/bin/python -m pytest server/tests -q` → PASS.

- [ ] **Step 6: Commit** (with approval) — `git add -A server && git commit -m "feat(docs): server pipeline (extract → embed), legacy swap, resume vs re-index"`

---

### Task 9: Multipart registration with proof of possession; remove client chunk and byte routes

**Files:**
- Modify: `server/routers/docs.py`
- Create: `server/tests/test_docs_upload.py`
- Modify: tests that called the JSON register, `/chunks` or `/pdf`. Find them with `grep -rln '"/v1/docs"\|/chunks\|/pdf' server/tests`.

**Interfaces:**
- Consumes: `doc_storage.*` (Task 3), `doc_content.add_entry` (Task 6), `doc_pipeline.run_pipeline` and `run_legacy_swap` (Task 8), `sha256_file` (Task 3).
- Produces: `POST /v1/docs` (multipart: `file`, optional `file_name`, repeated `tags`, `client_doc_id`) returning `{doc_id, dedup: bool, state}`.
  - **202** when a job was scheduled;
  - **200** for a pure dedupe;
  - 413 / 415 / 422 refusals.

- [ ] **Step 1: Failing tests** in `server/tests/test_docs_upload.py`, using `build_docs_app`. Post with `files={"file": (name, bytes)}` and `data={...}`. Cover these cases:
  - **new text file** → 202, `dedup` false; after the call, GET shows `indexed`; the bytes file exists at `<store>/<sha>.txt`; `documents.extracted_by == 'server'`.
  - **the same bytes from a second user** → **200**, `dedup` true, `state == "indexed"`; the fake embed is **not called again** (count calls with a wrapper); two entries; chunk count unchanged.
  - **server hash wins**: `client_doc_id="0"*64` → the response `doc_id` is the real sha, and a WARNING containing "hint mismatch" is logged (`caplog`).
  - **the same user uploads twice** with `tags=["a"]`, then `tags=["b"]` → still one entry, tags `["b"]`. *(Review Focus 5)*
  - **a shared-entry holder uploads the bytes** → their entry becomes `upload`. *(Review Focus 5)*
  - **empty file** → 422 `empty_file`; no `documents` row. *(Review Focus 1)*
  - **text with NUL** → 415. **`.pdf` without magic** → 415. **Oversize** (`TEXT_UPLOAD_MAX_MB=1`) → 413 with `limit_mb == 1`.
  - **An ID is not access:** another user's doc id, without the bytes → GET/search/markdown → 404, and `PUT /shares/...` → 404.
  - **Removed routes:** `POST /v1/docs/{id}/chunks`, `POST /v1/docs/{id}/pdf` and `DELETE /v1/docs/{id}/pdf` → 404 or 405.
  - **Legacy, indexed:** seed `extracted_by='client'`, `state='indexed'`, `bytes_path=None`; upload the matching bytes → 200 dedupe; afterwards `extracted_by='server'` and the chunks were re-extracted.
  - **Legacy, not indexed** (`state='extracted'`, client) → 202, then `indexed` and `extracted_by='server'`.
  - **Legacy, converted, stored bytes already hash to the doc id** → `extracted_by` flips to `'server'` with **no** change to the chunks and no embed call. The conversion is kept.
  - **Self-heal:** content whose `bytes_path` points to a deleted file; upload the bytes → the file exists again, and a WARNING containing "missing bytes" is logged.
  - **Lost race with GC:** monkeypatch `docs_router._lock_existing` to return `None` on its first call only → the response is 202 (the retry created the content), and a WARNING containing "retry" is logged.
  - **Filename edge** `"../../x.pdf"` → stored under `<store>/<sha>.pdf`; the entry shows `file_name == "../../x.pdf"` as a display name. *(Review Focus 4)*

  Ruling on the spec's "concurrent same-hash uploads" test: it is covered deterministically, by the dedupe case plus the lost-race retry case. Two-connection timing tests are flaky. Correctness rests on `INSERT … ON CONFLICT` plus `FOR SHARE` against GC's `FOR UPDATE`, which Postgres guarantees, and these tests exercise every branch of our code around them.

  Run → FAIL.

- [ ] **Step 2: Replace `register_document` and delete the old routes**

  Delete: `DocRegisterIn`, `ChunkIn`, `ChunksUploadIn`, `upload_chunks`, `upload_pdf_bytes`, `delete_pdf_bytes`, `_pdf_storage_path`, `PDF_STORAGE_DIR`, `PDF_UPLOAD_MAX_MB`. Also remove `_require_upload_holder` if no route uses it any more (shares use it; keep it if so). Add `Form` to the `fastapi` imports and `from ..services import doc_storage`. Then:

```python
async def _lock_existing(conn, doc_id: str):
    """Lock existing content FOR SHARE before adding an entry, so a concurrent
    GC (FOR UPDATE) either finishes first — we then see no row — or waits for
    us. Returns (state, extracted_by, bytes_path, conversion_state) or None."""
    cur = await conn.execute(
        "SELECT state, extracted_by, bytes_path, conversion_state FROM documents "
        "WHERE doc_id = %s FOR SHARE", (doc_id,))
    return await cur.fetchone()


@router.post("")
async def register_document(
    background: BackgroundTasks,
    response: Response,
    file: UploadFile = File(...),
    file_name: str | None = Form(None),
    tags: list[str] = Form(default=[]),
    client_doc_id: str | None = Form(None),
    principal: Principal = Depends(require_capability("reader")),
) -> dict[str, Any]:
    """Add a file to my library (A1 spec §4). The server hashes the uploaded
    bytes — that hash IS the doc id; `client_doc_id` is only a hint. Existing
    content → 200 dedupe, no rework. New content → 202 and a background
    extract + embed job. Proof of possession: nobody gets an entry without
    sending the bytes."""
    _ensure_ready()
    name = (file_name or file.filename or "").strip()[:255] or "document"
    staged = await doc_storage.stage_upload(file, name)
    try:
        doc_id = staged.sha256
        if client_doc_id and client_doc_id != doc_id:
            logger.warning("Client hash hint mismatch for user %s: hint %s, server %s",
                           principal.user_id, client_doc_id, doc_id)
        created, existing = await _add_upload_entry(principal.user_id, staged, name, tags)
        job = await _after_upload(doc_id, staged, created, existing)
    finally:
        doc_storage.discard(staged)  # no-op once placed
    if job is not None:
        background.add_task(job, doc_id)
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
        state = (await cur.fetchone())[0]
    scheduled_new_work = job is doc_pipeline.run_pipeline
    response.status_code = 202 if scheduled_new_work else 200
    return {"doc_id": doc_id, "dedup": not created, "state": state}


async def _add_upload_entry(user_id: str, staged, name: str, tags: list[str]):
    """Create-or-find the content and give the user an upload entry, in one
    transaction. Retries ONCE if a GC removed the row between our conflicting
    INSERT and our lock (spec §3, races) — the caller never sees a 5xx."""
    for attempt in (1, 2):
        async with get_pool().connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                    "extracted_by) VALUES (%s,%s,%s,%s,'extracting','server') "
                    "ON CONFLICT (doc_id) DO NOTHING RETURNING doc_id",
                    (staged.sha256, name, staged.file_type, staged.size))
                created = await cur.fetchone() is not None
                existing = None
                if not created:
                    existing = await _lock_existing(conn, staged.sha256)
                    if existing is None:
                        if attempt == 1:
                            logger.warning("Upload of %s lost a race with GC; retrying once",
                                           staged.sha256)
                            continue
                        raise refusal(503, "busy", "Please try again.")
                outcome = await doc_content.add_entry(
                    conn, user_id, staged.sha256, via="upload", tags=tags,
                    file_name=None if created else name)
        if outcome != "exists":
            audit("entry.added", user=user_id, doc=staged.sha256, via="upload",
                  dedup=str(not created).lower())
        return created, existing
    raise AssertionError("unreachable")
```

  (When the content already exists, the entry's `file_name` is set to the upload name on purpose: it shows the user their own name for the file. Spec §3: "optional personal `file_name`".)

```python
async def _after_upload(doc_id: str, staged, created: bool, existing):
    """Put verified bytes in place (after the INSERT committed) and decide
    what, if anything, runs next. Returns the job to schedule, or None."""
    if created:
        await _set_bytes_path(doc_id, doc_storage.place(staged, doc_id))
        return doc_pipeline.run_pipeline
    state, extracted_by, bytes_path, conversion_state = existing
    old_file_ok = bool(bytes_path) and doc_storage.sha256_file(Path(bytes_path)) == doc_id
    if not old_file_ok or extracted_by == "client":
        if bytes_path and not Path(bytes_path).exists():
            logger.warning("Content %s had missing bytes; restored from this upload", doc_id)
        await _set_bytes_path(doc_id, doc_storage.place(staged, doc_id))
    if extracted_by == "client":
        if state == "indexed":
            if conversion_state == "converted" and old_file_ok:
                # Its converted pages came from bytes that hash to the id: already
                # server-derived. Trust it without rework.
                async with get_pool().connection() as conn:
                    await conn.execute(
                        "UPDATE documents SET extracted_by = 'server', updated_at = now() "
                        "WHERE doc_id = %s", (doc_id,))
                return None
            return doc_pipeline.run_legacy_swap
        await _set_state(doc_id, "stored")
        return doc_pipeline.run_pipeline
    if state in ("stored", "failed", "registered"):
        await _set_state(doc_id, "stored")
        return doc_pipeline.run_pipeline
    if state == "extracted":
        return doc_pipeline.run_pipeline
    return None  # extracting / indexing / indexed: dedupe, nothing to redo


async def _set_bytes_path(doc_id: str, path: Path) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE documents SET bytes_path = %s, updated_at = now() WHERE doc_id = %s",
            (str(path), doc_id))


async def _set_state(doc_id: str, state: str) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE documents SET state = %s, error_message = NULL, updated_at = now() "
            "WHERE doc_id = %s", (state, doc_id))
```

  Status code: 200 for a pure dedupe. That includes the legacy swap, which serves the old index while it runs, and the converted-legacy flip. 202 whenever `run_pipeline` was scheduled. The `scheduled_new_work` line above implements exactly that.

- [ ] **Step 3: Update the older tests** that registered via JSON or used `/chunks` / `/pdf`. They should seed through `seed.seed_doc`, or upload via multipart with `build_docs_app`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest server/tests -q` → PASS. Then `grep -n "chunks\b\|/pdf" server/routers/docs.py`: only comments may mention them.

- [ ] **Step 5: Commit** (with approval) — `git add -A server && git commit -m "feat(docs): multipart upload with server hash, dedupe without rework; drop client chunk/byte routes"`

---

### Task 10: Content-changing ops: sole holder or admin

**Files:**
- Modify: `server/routers/docs.py`
- Create: `server/tests/test_content_ops.py`

**Interfaces:**
- Consumes: `_content_shared` and `doc_content.content_ops_refusal`.
- Produces: `_require_content_op` dependency. `DELETE /{id}/markdown` now re-extracts natively (state → `extracting`).

- [ ] **Step 1: Failing tests** (`test_content_ops.py`, with `build_docs_app`; set `DOCLING_ENABLED` via `monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)`, and stub `docling_convert.convert_pdf_to_markdown_pages` to return `[(1, "# P1")]`):
  - sole holder: `POST /convert` → 202; after it, `conversion_state == 'converted'`;
  - a second holder exists → both holders get 409 `content_shared` / `other_holders`;
  - an admin (`role="admin"`) who holds nothing but can read via a project → 202;
  - the sole holder with their own placement → 409 `in_project`;
  - `DELETE /markdown` by the sole holder → 200. Afterwards `doc_pages` is empty, `conversion_state` is NULL, and the state ends `indexed` via native re-extraction: chunks are `page` / `block`, not `page-md`;
  - `DELETE /markdown` with another holder → 409;
  - a non-reader → 404 on both routes.

- [ ] **Step 2: Implement**

```python
async def _require_content_op(doc_id: DocId,
                              principal: Principal = Depends(_require_doc_reader)) -> Principal:
    """Content-changing ops (convert, delete converted markdown) change the
    document for everyone who uses it: only the sole holder or an admin
    (spec §5). Readers who may not get 409 content_shared with a reason;
    non-readers already got 404 from _require_doc_reader."""
    async with get_pool().connection() as conn:
        reason = await doc_content.content_ops_refusal(
            conn, doc_id, principal.user_id, is_admin=principal.role == "admin")
    if reason:
        raise _content_shared(reason, doc_id, principal.user_id)
    return principal
```

  - Use `_require_content_op` on `POST /{doc_id}/convert` and `DELETE /{doc_id}/markdown`.
  - In `start_convert_job`, change the 409 message for missing bytes to `refusal(409, "bytes_missing", "Upload the file again first.")`.
  - `delete_document_markdown`: in the same transaction, set `state = 'stored'` (not `'registered'`), then `background.add_task(doc_pipeline.run_pipeline, doc_id)`. Add a `background: BackgroundTasks` parameter and return `{"ok": True, "doc_id": doc_id, "state": "extracting"}`.
  - Update the docstring: deleting the converted markdown falls back to the server's native extraction.

- [ ] **Step 3: Run** `.venv/bin/python -m pytest server/tests -q` → PASS.

- [ ] **Step 4: Commit** (with approval) — `git commit -am "feat(docs): content-changing ops need the sole holder or an admin (409 content_shared)"`

---

### Task 11: Frontend: refusal notices, upload flow, new states

**Files:**
- Create: `src/lib/apiErrors.js`, `src/lib/apiErrors.test.js`, `src/components/IndexButton.test.jsx`
- Modify: `src/lib/docMeta.js`, `src/lib/docMeta.test.js`, `src/App.jsx`, `src/components/IndexButton.jsx`, `src/hooks/useChatEngine.js` (comment only)
- Delete: `src/lib/uploadPdf.js`

**Interfaces:**
- Produces: `noticeFor(status, detail) -> string` and `describeRefusal(res) -> Promise<string>`.
- Produces: `registerDocument({apiHost, apiPort, file, fileName, clientDocId, projectId, tags, linkProject = true}) -> {docId, dedup, state}`. It throws `Error(notice)` on a refusal.
- Produces: `linkDocToProject({apiHost, apiPort, projectId, docId})`, fail-soft.

- [ ] **Step 1: Failing tests.** `src/lib/apiErrors.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { noticeFor, describeRefusal } from './apiErrors';

describe('noticeFor', () => {
    it('explains both content_shared reasons', () => {
        expect(noticeFor(409, { error: 'content_shared', reason: 'other_holders' })).toMatch(/Other people also use/);
        expect(noticeFor(409, { error: 'content_shared', reason: 'in_project' })).toMatch(/Remove it from the project first/);
    });
    it('maps size, type, empty, missing and server errors', () => {
        expect(noticeFor(413, { error: 'too_large', limit_mb: 10 })).toBe('File too large (limit 10 MB).');
        expect(noticeFor(415, { error: 'unsupported_type' })).toBe('Unsupported file type.');
        expect(noticeFor(422, { error: 'empty_file' })).toBe('The file is empty.');
        expect(noticeFor(404, 'Document not found')).toBe("This document doesn't exist or you don't have access.");
        expect(noticeFor(502, null)).toBe('Something went wrong on the server. Try again.');
    });
    it('falls back to the server message, then the status', () => {
        expect(noticeFor(409, { error: 'bytes_missing', message: 'Upload the file again first.' })).toBe('Upload the file again first.');
        expect(noticeFor(418, null)).toBe('Request failed (HTTP 418).');
    });
});

describe('describeRefusal', () => {
    it('reads detail from the body and survives a non-JSON body', async () => {
        const ok = { status: 415, json: async () => ({ detail: { error: 'unsupported_type' } }) };
        expect(await describeRefusal(ok)).toBe('Unsupported file type.');
        const bad = { status: 500, json: async () => { throw new Error('html'); } };
        expect(await describeRefusal(bad)).toBe('Something went wrong on the server. Try again.');
    });
});
```

  Rewrite `src/lib/docMeta.test.js`'s `registerDocument` block:
  - POST `/v1/docs` carries a `FormData` body with `file`, `file_name`, `client_doc_id`, and one `tags` entry per tag;
  - there is **no** PATCH;
  - the project PUT happens after the POST when `projectId` is set, and not when `linkProject: false`;
  - a 415 response throws `Unsupported file type.`;
  - the return value is `{docId: <server id>, dedup, state}`.

  Keep the `parseTagsInput` tests.

  `src/components/IndexButton.test.jsx`:
  - `extracting` → a disabled button with the text "Extracting";
  - `stored` and `extracted` → an enabled "Resume" button that calls `onIndex`;
  - `indexing` shows `3/9`;
  - `indexed`, `failed` and idle render as before.

  Run: `npx vitest run src/lib src/components/IndexButton.test.jsx` → FAIL.

- [ ] **Step 2: Implement `src/lib/apiErrors.js`**

```js
/**
 * One place that turns a refused API call into a notice a person can act on
 * (A1 spec §8; A0 adds its codes here). Server refusals arrive as
 * { detail: { error, message, ...extra } }; older routes send a string detail.
 * Only a 5xx blames the server — a 404/409 is a permission or state problem,
 * and saying "server error" there sends people looking for the wrong fix.
 */
const CONTENT_SHARED = {
    other_holders: "Other people also use this document, so it can't be changed here. Ask an admin.",
    in_project: 'This document is in a project, so changing it would change it for the project too. Remove it from the project first, or ask an admin.',
};

export function noticeFor(status, detail) {
    const d = detail && typeof detail === 'object' ? detail : null;
    if (status === 409 && d?.error === 'content_shared') {
        return CONTENT_SHARED[d.reason] || CONTENT_SHARED.other_holders;
    }
    if (status === 413) return `File too large (limit ${d?.limit_mb ?? '?'} MB).`;
    if (status === 415) return 'Unsupported file type.';
    if (status === 422 && d?.error === 'empty_file') return 'The file is empty.';
    if (status === 404) return "This document doesn't exist or you don't have access.";
    if (status >= 500) return 'Something went wrong on the server. Try again.';
    if (d?.message) return d.message;
    if (typeof detail === 'string' && detail) return detail;
    return `Request failed (HTTP ${status}).`;
}

export async function describeRefusal(res) {
    let detail = null;
    try {
        detail = (await res.json())?.detail ?? null;
    } catch {
        // non-JSON error body (proxy page, empty) — fall back to the status
    }
    return noticeFor(res.status, detail);
}
```

- [ ] **Step 3: Rewrite `registerDocument` in `src/lib/docMeta.js`**

  Update the doc comment to say that registration uploads the file. The server hashes it, dedupes, and extracts the text; tags travel with the upload; a project link follows only when asked.

```js
export async function registerDocument({
    apiHost, apiPort, file, fileName, clientDocId, projectId, tags, linkProject = true,
}) {
    const form = new FormData();
    form.append('file', file, fileName);
    form.append('file_name', fileName);
    if (clientDocId) form.append('client_doc_id', clientDocId);
    for (const t of tags || []) form.append('tags', t);
    const res = await apiFetch(apiHost, apiPort, '/v1/docs', { method: 'POST', body: form });
    if (!res.ok) throw new Error(await describeRefusal(res));
    const body = await res.json();
    const result = { docId: body.doc_id, dedup: !!body.dedup, state: body.state };
    if (projectId && linkProject) {
        await linkDocToProject({ apiHost, apiPort, projectId, docId: result.docId });
    }
    return result;
}

// Fail-soft: the document is in the library either way; a failed link is
// logged and must not block the indexing/conversion that follows.
export async function linkDocToProject({ apiHost, apiPort, projectId, docId }) {
    try {
        const res = await apiFetch(
            apiHost, apiPort,
            `/v1/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(docId)}`,
            { method: 'PUT' },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
        console.error('Doc project link failed:', e);
    }
}
```

  Import `describeRefusal` from `./apiErrors`.

- [ ] **Step 4: Rewrite the two flows in `src/App.jsx`.** Replace `handleIndexDocument` (currently ~lines 720–838) with the following, plus a shared poller:

```js
  // Doc states the server moves through on its own (A1 spec §4 States).
  const PROCESSING_STATES = new Set(['stored', 'extracting', 'extracted', 'indexing']);

  const pollIndexUntilSettled = useCallback(async (docId) => {
    const POLL_MS = 2000;
    const MAX_POLLS = 300; // ~10 minutes
    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      try {
        const sRes = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`);
        if (!sRes.ok) continue;
        const sData = await sRes.json();
        setDocIndexByDocId((prev) => ({
          ...prev,
          [docId]: { state: sData.state, chunkCount: sData.chunk_count, embeddedCount: sData.embedded_count },
        }));
        if (sData.state === 'indexed') { showToast(`Indexed ${sData.embedded_count} chunks.`, 3000); return; }
        if (sData.state === 'failed') { showToast(`Indexing failed: ${sData.error_message || 'unknown error'}`, 6000); return; }
        if (!PROCESSING_STATES.has(sData.state)) return;
      } catch {
        // transient backend hiccup — keep polling
      }
    }
    showToast('Indexing is taking unusually long — check the server logs.', 6000);
  }, [apiHost, apiPort, showToast]);

  const handleIndexDocument = useCallback(async () => {
    if (!pdfFileName) return;
    const docId = await ensureDocHash();
    if (!docId) { showToast('Could not read document bytes — re-open the file and try again.', 4000); return; }
    const setIndex = (id, patch) =>
      setDocIndexByDocId((prev) => ({ ...prev, [id]: { ...(prev[id] || {}), ...patch } }));

    // Re-index an indexed doc: the server re-extracts from its stored bytes.
    // Only the sole holder or an admin may — others get a notice saying why.
    if (docIndexByDocId[docId]?.state === 'indexed') {
      const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}/index`, { method: 'POST' });
      if (!res.ok) { showToast(await describeRefusal(res), 6000); return; }
      setIndex(docId, { state: 'extracting' });
      await pollIndexUntilSettled(docId);
      return;
    }

    setIndex(docId, { state: 'uploading' });
    let result;
    try {
      const record = await getBook(pdfFileName);
      if (!record?.data) throw new Error('File is not in the local library — re-open it and try again.');
      result = await registerDocument({
        apiHost, apiPort, file: new Blob([record.data]), fileName: pdfFileName,
        clientDocId: docId, projectId: docProjectId, tags: parseTagsInput(docTagsText),
      });
    } catch (e) {
      setIndex(docId, { state: 'failed' });
      showToast(`Indexing failed: ${e.message}`, 6000);
      return;
    }
    // The server's hash is authoritative (spec §8); it equals ours for the same bytes.
    const serverId = result.docId;
    if (serverId !== docId) console.warn('Server doc id differs from local hash', { docId, serverId });
    setIndex(serverId, { state: result.state });
    if (result.dedup && result.state === 'indexed') {
      showToast('Already indexed — added to your library.', 4000);
      return;
    }
    await pollIndexUntilSettled(serverId);
  }, [pdfFileName, ensureDocHash, docIndexByDocId, showToast, apiHost, apiPort, docProjectId, docTagsText, pollIndexUntilSettled]);
```

  In `handleConvertDocument`:
  - step 1 calls `registerDocument({... file: new Blob([record.data], { type: 'application/pdf' }), fileName: pdfFileName, clientDocId: docId, tags: parseTagsInput(docTagsText), linkProject: false })`, reading `record` via `getBook`;
  - use `result.docId` from then on;
  - **delete step 2** (`uploadPdfBytesToBackend`);
  - on a non-OK convert kick-off, throw `new Error(await describeRefusal(res))` so a 409 shows the sole-holder notice;
  - right after a successful kick-off, `if (docProjectId) linkDocToProject({ apiHost, apiPort, projectId: docProjectId, docId: result.docId })`. The link has to come after the kick-off: an earlier placement would make the uploader's own convert fail with `in_project`.

  Other changes:
  - Remove the `uploadPdf` import and `git rm src/lib/uploadPdf.js`.
  - Import `describeRefusal` and `linkDocToProject`.
  - Update the state comment at `App.jsx:154` to `'idle' | 'uploading' | 'stored' | 'extracting' | 'extracted' | 'indexing' | 'indexed' | 'failed'`.
  - Update `useChatEngine.js:55`'s comment the same way.

- [ ] **Step 5: Rewrite `src/components/IndexButton.jsx`'s state branches**

  Keep the existing markup, classes and icons.
  - `uploading`: unchanged. Change its title to "Uploading the file…".
  - `stored` / `extracting`: a disabled spinner button with the label **"Extracting"** and the title "The server is reading the document's text".
  - `indexing`: unchanged.
  - `indexed`: unchanged. Title: "Indexed (N chunks). Click to re-index — only if nobody else uses it."
  - `extracted`: an amber, **clickable** "Resume" button with the title "Indexing was interrupted — click to resume".
  - `failed`: unchanged.
  - Delete the `chunks_uploaded` branch.
  - Update the header comment's state list.

  Note: `stored` is reached only after a crash, and nothing runs until someone clicks. Make it clickable too: put `stored` in the Resume branch, not the spinner branch. The spinner branch is `extracting` only.

- [ ] **Step 6: Run** `npx vitest run && npm run lint` → PASS, no new lint errors.

- [ ] **Step 7: Commit** (with approval) — `git add -A src && git commit -m "feat(reader): upload files for server-side indexing; refusal notices; new doc states"`

---

### Task 12: Library page: remove from my library, badges, chip rules

**Files:**
- Modify: `src/components/library/LibraryPage.jsx`, `LibraryPage.test.jsx`, `LibraryPage.delete-busy.test.jsx`

**Interfaces:**
- Consumes the Task 6 row shape `{doc_id, file_name, state, tags, projects, in_library, added_via, shared_by: {id,name}|null}`, the project shape `{id, name, is_owner, …}`, and `describeRefusal` (Task 11).

- [ ] **Step 1: Failing tests.**
  - Update the fixtures in `LibraryPage.test.jsx`:
    - `d1`: `{…, in_library: true, added_via: 'upload', shared_by: null}`;
    - `d2`: `{…, in_library: true, added_via: 'shared', shared_by: { id: 'u2', name: 'Ann' }}`;
    - new `d3`: `{…, in_library: false, added_via: null, shared_by: null, projects: [{ id: 'p1', name: 'Project A' }]}`.
  - Assert:
    - d1 shows a remove button labelled `Remove Owned.pdf from my library`, a tag editor and a `+ project` select;
    - d2 shows "shared by Ann", a remove button and a tag editor, but **no** `+ project` select (you can't file something you didn't upload);
    - d3 shows "via project", **no** remove button and no tag editor;
    - the × on a chip renders only for projects with `is_owner` (p1), never for p2, even on d1;
    - confirming a removal shows the text "People and projects that have it keep their copies" and sends `DELETE /v1/docs/d1`;
    - a failed unlink with a 404 body shows the `describeRefusal` notice, not "HTTP 404".
  - Update `LibraryPage.delete-busy.test.jsx` to the new labels ("Removing…", "Confirm remove").

  Run: `npx vitest run src/components/library` → FAIL.

- [ ] **Step 2: Implement.**
  - `ProjectChips`: `ownedProjectIds` is unchanged. Show the × only when `ownedProjectIds.has(p.id)`. Render the `+ project` select only when `doc.added_via === 'upload' && addable.length > 0`. Update its comment: projects govern their documents; only an uploader files in; only the project owner removes.
  - Row header badges replace `!doc.is_owner && …shared`:
    - `doc.added_via === 'shared'` → `<Share2/> shared by {doc.shared_by?.name || 'someone'}`;
    - `!doc.in_library` → `<FolderOpen/> via project`.
  - The remove control renders only when `doc.in_library`:
    - `aria-label` `Remove ${doc.file_name} from my library`;
    - the confirm row reads `Remove from your library? People and projects that have it keep their copies.`, then **Confirm remove** / **Cancel**;
    - while busy, "Removing…".
  - The tag editor renders when `doc.in_library`, and read-only tags otherwise.
  - Every `showToast(\`Update failed: ${e.message}\`)` / `Delete failed` path: when `!res.ok`, throw `new Error(await describeRefusal(res))`, and toast `e.message`.
  - Update the component doc comment: rows are my library (uploaded or shared with me) plus project documents; removing affects only my copy.

- [ ] **Step 3: Run** `npx vitest run && npm run lint` → PASS.

- [ ] **Step 4: Commit** (with approval) — `git add -A src && git commit -m "feat(library): remove from my library, shared-by and via-project badges, project chip rules"`

---

### Task 13: Configuration, docs, and journeys checked in the running app

**Files:**
- Modify: `.env.example`, `docs/LIBRARY.md`, `docs/ARCHITECTURE.md`, `README.md`

- [ ] **Step 1: `.env.example`.** Next to the other document settings, add:

```bash
# --- Document storage & uploads (A1) ---
# Where uploaded document bytes live (<dir>/<doc_id>.<ext>). Falls back to
# PDF_STORAGE_DIR (the pre-A1 name), then ./data/pdfs. Existing files stay put.
# DOC_STORAGE_DIR=./data/pdfs
# PDF_STORAGE_DIR=./data/pdfs
# Upload size caps in MB. Over the cap → HTTP 413.
# PDF_UPLOAD_MAX_MB=50
# TEXT_UPLOAD_MAX_MB=10

# Audit trail of who-holds-what changes (entries, shares, placements, GC),
# IDs only. Default: <LOG_DIR>/audit.log, rotated like server.log.
# LOG_AUDIT_FILE=./logs/audit.log
```

- [ ] **Step 2: `docs/LIBRARY.md`.** Replace the ownership model section with A1's: content / library entries / placements, and read access. Add these sections:
  - **Uploading:** the file is uploaded and the server hashes it; a known file is added instantly (no re-processing); a new one is extracted and indexed on the server.
  - **Removing:** only your copy goes; people and projects keep theirs.
  - **Sharing:** say plainly that **sharing a single document is API-only until the project-management UI (A0) ships.** Include the two `curl` calls (`PUT` / `DELETE /v1/docs/{id}/shares/{user_id}`), and the rules: only someone who uploaded the file can share it, recipients can't re-share, and revoking removes only shares you created.
  - **Projects govern their documents:** only the project owner (after A0, Maintainers) removes a document from a project.
  - **"Why can't I re-convert?":** convert, delete converted markdown and re-index need you to be the **only** user of the document. That includes projects, *even ones you filed it into yourself*. Remove it from the project first, or ask an admin.
  - **Upgrade notes (migration 011):**
    - back up first (`pg_dump`);
    - the SPA and backend must deploy together;
    - existing owners become upload entries and grants become shared entries;
    - documents indexed before A1 are re-extracted once, the first time someone uploads the file again. A conversion made from unverified bytes is discarded then.
- [ ] **Step 3: `docs/ARCHITECTURE.md`.** Update the document data model, the upload → extract → embed pipeline, the state list, `DOC_STORAGE_DIR`, the audit log, and the removal of `/chunks` and `/pdf`.
- [ ] **Step 4: `README.md:90`.** Replace the Document Library bullet with:

  > **Document Library** — Documents are private by default. The server verifies every upload, and a file someone already indexed is added to your library instantly. Share through projects, or with one person via the API (a sharing screen arrives with project management). A **Library** view lists, searches and filters your documents by project and tags, and shows what was shared with you and by whom. Removing a document only removes your copy. Non-readers get a 404, never a hint that the document exists.

- [ ] **Step 5: Full suite** — `.venv/bin/python -m pytest server/tests -q && npx vitest run && npm run lint` → all pass. Record the counts.

- [ ] **Step 6: Walk the six journeys in the running app** (`./startup.sh up`, then two browser profiles or two users; the `chrome-devtools` or `playwright` MCP tools work too). For each journey, write down what you saw.
  1. User A indexes `sample.pdf`, and it reaches Indexed. User B opens the same file and clicks **Index**. B sees "Already indexed — added to your library" at once. The server log shows no new extraction or embedding for that doc.
  2. A new `.md` file: Index → "Extracting" → "Indexing n/m" → Indexed. Ask chat a question about it: the citation opens the right reader page.
  3. B: Library → **Remove from my library** → confirm. A still has it. Any project that holds it still lists it.
  4. A shares with B **via curl**, and B's Library shows "shared by A". Say in the summary that there is no share button until A0.
  5. A files the doc into A's project. B (a member) sees it "via project" with no remove control. Only A (the project owner) sees the chip's ×.
  6. With A and B both holding the doc, B clicks **Convert** and sees "Other people also use this document…". A alone (after B removes theirs), with the doc still in A's project, sees the `in_project` notice.

- [ ] **Step 7: Commit** (with approval) — `git add -A && git commit -m "docs(library): A1 content/entries model, upload + sharing notes, upgrade notes"`

**Final summary requirement (global rule):** answer, "Can the user do what they asked for, start to finish, without curl?" The honest answer is: every journey except single-person sharing (journey 4), which is API-only until A0.
