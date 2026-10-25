"""The stored outline of a document (C2.1 spec 5.3, migration 021).

One row per content in `doc_outlines`, keyed by the content's hash, so every
holder and every project the document sits in shares it. It holds the
headings (or PDF bookmarks) with their pages, plus the document's opening
line for a document that has no outline. Extraction produces it
(services.extract); doc_pipeline saves it in the same transaction as the
chunk swap, so a document never shows chunks from one extraction and an
outline from another."""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Jsonb

from .extract import OutlineEntry

# doc_outlines.source: where the entries came from.
HEADINGS, BOOKMARKS, NO_OUTLINE = "headings", "bookmarks", "none"


@dataclass(frozen=True)
class Outline:
    entries: tuple[OutlineEntry, ...]
    opening: str     # first characters of the first prose chunk (extract.opening_line)
    source: str      # HEADINGS | BOOKMARKS | NO_OUTLINE


async def save_outline(conn, doc_id: str, outline: Outline) -> None:
    """Insert the document's outline, or replace the one it has."""
    entries = [{"level": e.level, "text": e.text, "page": e.page} for e in outline.entries]
    await conn.execute(
        "INSERT INTO doc_outlines (doc_id, entries, opening, source) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (doc_id) DO UPDATE SET entries = EXCLUDED.entries, opening = EXCLUDED.opening, "
        "source = EXCLUDED.source, created_at = now()",
        (doc_id, Jsonb(entries), outline.opening, outline.source))


async def load_outlines(conn, doc_ids: list[str]) -> dict[str, Outline]:
    """The stored outlines of `doc_ids`. A document with none is absent."""
    if not doc_ids:
        return {}
    cur = await conn.execute(
        "SELECT doc_id, entries, opening, source FROM doc_outlines WHERE doc_id = ANY(%s)", (list(doc_ids),))
    return {doc_id: Outline(tuple(OutlineEntry(e["level"], e["text"], e["page"]) for e in entries),
                            opening, source)
            for doc_id, entries, opening, source in await cur.fetchall()}
