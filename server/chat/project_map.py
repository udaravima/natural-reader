"""The project map (C2.1 spec 5.2): the assistant's first look at a project,
before it searches. One line group per document, in the scope's order:

    <project_documents project="Natural Reader docs">
    1. "README.md" — 9 pages. Outline: Features p1 · Quick start p2 · Configuration p4
    2. "DEPLOYMENT.md" — 14 pages — open in the reader. Outline: Requirements p1 · Containers p3
    3. "Annual scan.pdf" — 40 pages. No outline; starts: "Annual report 2024. Prepared by …"
    4. "Notes.txt" — being indexed, not searchable yet.
    </project_documents>

The number is the document's `ref` in every tool call and result, so the model
can choose where to look and then say so ("documents": [2]). Two-stage
routing: pick documents here, sections from the outlines, then read.

Everything a document or a user wrote (names, headings, openings, the
project's name) goes through `display_name`, one inert line, so none of it can
close the tag or speak as us; the data rule names the tag (prompt.py). A
document's name already did, in the scope (ScopeDoc), and is shown as it is:
applying display_name again would cut off a long name's " (2)".

Only an INDEXED document's outline and opening are shown. The revert route
deletes a document's chunks but leaves its `doc_outlines` row, so a stale
outline can outlive its chunks; a document that isn't indexed shows its state
and nothing else.

The map is a pure function of the scope and the outlines: the same input gives
the same bytes, so it stays put in the prefix cache from turn to turn. When it
is over budget it is shortened in steps (STEPS: drop level 3, cap each outline
at its first 8 entries, drop level 2, then names and pages only), stopping at
the first that fits; once anything is cut it says so, and the `document_outline`
tool is offered for the turn (tools/document_outline.py) so the full outline is
one call away (on a turn that offers no tools, the note names no tool). The one thing it never cuts is a document: with hundreds, the
last step can still be over budget, and a document the model isn't told about
is one it can't search.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from ..services.doc_outlines import Outline
from .project_scope import ProjectScope, ScopeDoc
from .prompt import PROJECT_TAG, display_name

SHORTENED_LINE = "Outlines are shortened: call document_outline for a document's full outline."
# The same fact without the tool, for a turn that offers no tools: a tool is named only when it is offered.
SHORTENED_LINE_NO_TOOL = "Outlines are shortened."
FAILED = "failed"
INDEXED = "indexed"


@dataclass(frozen=True)
class ProjectMap:
    text: str          # the whole block, tags included, ready for the system message
    shortened: bool    # an outline or an opening was left out: document_outline is offered


class _Step(NamedTuple):
    max_level: int         # headings deeper than this are left out
    max_entries: int | None    # at most this many headings per document
    detail: bool           # False: names and pages only (no outline, no opening)


FIRST_ENTRIES = 8
# From the most to the least; each step is cumulative: drop level 3; cap each outline at its first 8 entries
# while level 2 is still kept; drop level 2 as well (the cap still applies); then names and pages only.
# The cap comes BEFORE level 2 goes because a Markdown outline has ONE level-1 heading (the title) and its
# sections at level 2: dropping level 2 first would leave titles only, and the cap nothing to cut.
STEPS = (
    _Step(3, None, True),
    _Step(2, None, True),
    _Step(2, FIRST_ENTRIES, True),
    _Step(1, FIRST_ENTRIES, True),
    _Step(0, 0, False),
)


def _pages(count: int | None) -> str:
    if not count:
        return "? pages"
    return "1 page" if count == 1 else f"{count} pages"


def _outline_items(outline: Outline, step: _Step) -> list[str]:
    items = []
    for e in outline.entries:
        if e.level > step.max_level:
            continue
        text = display_name(e.text)
        if text:
            items.append(f"{text} p{e.page}")
    return items if step.max_entries is None else items[:step.max_entries]


def _line(d: ScopeDoc, outline: Outline | None, step: _Step) -> str:
    # The scope's name as it is, already inert: the name the scope note saves and citations are matched against.
    head = f'{d.ref}. "{d.name}"'
    open_mark = " — open in the reader" if d.open_in_reader else ""
    state = d.doc.state
    if state != INDEXED:
        what = ("failed to index, not searchable" if state == FAILED
                else "being indexed, not searchable yet")   # stored, extracting, extracted, indexing, reindexing
        return f"{head} — {what}{open_mark}."
    line = f"{head} — {_pages(d.doc.page_count)}{open_mark}"
    if not step.detail:
        return line
    if outline is not None:
        items = _outline_items(outline, step)
        if items:
            return f"{line}. Outline: {' · '.join(items)}"
        if outline.entries:
            return line   # it has headings, and this step cut them all: claim neither an outline nor none
        opening = display_name(outline.opening)
        if opening:
            return f'{line}. No outline; starts: "{opening}"'
    return f"{line}. No outline."


def _body(scope: ProjectScope, outlines: dict[str, Outline], step: _Step) -> list[str]:
    lines = [_line(d, outlines.get(d.doc.doc_id) if d.doc.state == INDEXED else None, step)
             for d in scope.docs]
    return lines or ["The project has no documents yet."]


def build_map(scope: ProjectScope, outlines: dict[str, Outline], budget_chars: int, *,
              tool_offered: bool = True) -> ProjectMap:
    """The map of `scope`, within `budget_chars` characters (tags and the note about shortening
    included) if any step of the ladder fits, else the last step. `outlines` is keyed by doc_id.
    `tool_offered`: this turn offers document_outline. When the turn offers no tools at all (a model without
    tool support, or no tool rounds), the note says only that outlines are shortened, and names no tool."""
    note = SHORTENED_LINE if tool_offered else SHORTENED_LINE_NO_TOOL
    head = f'<{PROJECT_TAG} project="{display_name(scope.name)}">'
    tail = f"</{PROJECT_TAG}>"
    first: list[str] | None = None
    for step in STEPS:
        body = _body(scope, outlines, step)
        first = body if first is None else first
        shortened = body != first
        text = "\n".join([head, *body, *([note] if shortened else []), tail])
        if len(text) <= budget_chars:
            break
    return ProjectMap(text, shortened)
