"""The documents the document tools may read this turn, in `ref` order
(ref = position + 1). This slice: the one open, indexed document. C2 widens
it (a project, the library) here, for every document tool at once."""
from __future__ import annotations

from ...services.doc_search import ReadableDoc
from ..prompt import display_name, version_note_text


def document_scope(ctx) -> list[ReadableDoc]:
    return [ctx.doc] if ctx is not None and ctx.doc is not None and ctx.doc.state == "indexed" else []


def documents_listing(scope: list[ReadableDoc]) -> list[dict]:
    """How a result names its documents: a short ref plus the reader's name,
    and a version note when the document is an old version (V1 spec §7)."""
    items = []
    for ref, d in enumerate(scope, start=1):
        item = {"ref": ref, "name": display_name(d.name)}
        if d.version_note:
            item["version_note"] = version_note_text(d.version_note)
        items.append(item)
    return items
