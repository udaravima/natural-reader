"""The documents the document tools may read this turn, in `ref` order: a
project chat's searchable documents (C2.1), else the one open, indexed
document as ref 1. Widening the scope happens here, for every document tool
at once."""
from __future__ import annotations

from typing import Any

from ..project_scope import ScopeDoc
from ..prompt import display_name, version_note_text


def scope_docs(ctx) -> list[ScopeDoc]:
    if ctx is None:
        return []
    if ctx.project is not None:
        return list(ctx.project.searchable)
    if ctx.doc is not None and ctx.doc.state == "indexed":
        return [ScopeDoc(1, ctx.doc, display_name(ctx.doc.name), True)]
    return []


def resolve_document(scope: list[ScopeDoc], value: Any) -> ScopeDoc | None:
    """The document a tool argument names: its ref (2, "2"; 2.0 from small
    models), or its exact name, compared case-insensitively."""
    ref = None
    if isinstance(value, int) and not isinstance(value, bool):
        ref = value
    elif isinstance(value, float) and value.is_integer():
        ref = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        ref = int(value.strip())
    if ref is not None:
        found = next((d for d in scope if d.ref == ref), None)
        if found is not None or not isinstance(value, str):
            return found
    if isinstance(value, str):   # also a name that is all digits ("2024")
        key = value.strip().casefold()
        return next((d for d in scope if d.name.casefold() == key), None)
    return None


def resolve_documents(scope: list[ScopeDoc], values: Any) -> tuple[list[ScopeDoc], list[str]]:
    """`resolve_document` for a list: (found, unknown). `found` has each document once, in the order the
    values name them; `unknown` holds what matched nothing, as one inert line each (it goes into an error
    the model reads, and a name is someone else's text)."""
    found: list[ScopeDoc] = []
    unknown: list[str] = []
    for value in values:
        doc = resolve_document(scope, value)
        if doc is None:
            unknown.append(display_name(value))
        elif doc not in found:
            found.append(doc)
    return found, unknown


def documents_listing(scope: list[ScopeDoc]) -> list[dict]:
    """How a result names its documents: a short ref plus the reader's name,
    and a version note when the document is an old version (V1 spec §7)."""
    items = []
    for d in scope:
        item = {"ref": d.ref, "name": d.name}
        if d.doc.version_note:
            item["version_note"] = version_note_text(d.doc.version_note)
        items.append(item)
    return items


def choices_text(scope: list[ScopeDoc]) -> str:
    """The valid documents, for an error the model can recover from."""
    return ", ".join(f"{d.ref} {d.name}" for d in scope)
