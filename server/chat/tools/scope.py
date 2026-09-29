"""The documents the document tools may read this turn, in `ref` order
(ref = position + 1). This slice: the one open, indexed document. C2 widens
it (a project, the library) here, for every document tool at once."""
from __future__ import annotations

from ...services.doc_search import ReadableDoc
from ..prompt import display_name


def document_scope(ctx) -> list[ReadableDoc]:
    return [ctx.doc] if ctx is not None and ctx.doc is not None and ctx.doc.state == "indexed" else []


def documents_listing(scope: list[ReadableDoc]) -> list[dict]:
    """How a result names its documents: a short ref plus the reader's name."""
    return [{"ref": ref, "name": display_name(d.name)} for ref, d in enumerate(scope, start=1)]
