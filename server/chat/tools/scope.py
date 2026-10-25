"""The documents the document tools may read this turn, in `ref` order: a
project chat's searchable documents (C2.1), else the one open, indexed
document as ref 1. Widening the scope happens here, for every document tool
at once."""
from __future__ import annotations

import re
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


# A number, then the separator the map or a file name puts after it ("17. ", "17-", "17: "), then the rest.
_NUMBERED = re.compile(r"(\d+)\s*[-.:)_\s]\s*(.+)", re.S)


def _named(scope: list[ScopeDoc], name: str) -> ScopeDoc | None:
    key = name.strip().strip('"').strip().casefold()
    return next((d for d in scope if d.name.casefold() == key), None) if key else None


def resolve_document(scope: list[ScopeDoc], value: Any) -> ScopeDoc | None:
    """The document a tool argument names: its ref (2, "2"; 2.0 from small
    models), or its exact name, compared case-insensitively and with or
    without the map's quotes. Or both together, as the map lists them
    (`17. "README.md"`): next to names that start with their own number
    ("10-pbx.md") a model writes "17-README.md". The number and the name must
    then name the same document; nothing is guessed."""
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
        found = _named(scope, value)
        if found is None and (m := _NUMBERED.fullmatch(value.strip())):
            numbered = _named(scope, m.group(2))
            found = numbered if numbered is not None and numbered.ref == int(m.group(1)) else None
        return found
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
    return ", ".join(f'{d.ref}. "{d.name}"' for d in scope)   # the map's form (project_map._line)


def open_document(scope: list[ScopeDoc]) -> ScopeDoc | None:
    """The document the reader has open, when it is one of `scope` (a project's searchable documents): a project
    chat's default for a tool call that names no document. None when nothing is open, or the open document is
    not in the project, or is in it but isn't searchable yet."""
    return next((d for d in scope if d.open_in_reader), None)


def named_in_project(scope: list[ScopeDoc], value: Any, ctx, *, open_if_missing: bool = False) -> ScopeDoc | dict[str, str]:
    """The document `value` names in a project chat, or the error the model reads: required, in the
    project but not searchable yet, or unknown. `scope` is the searchable documents (scope_docs).
    `open_if_missing`: a call that names no document means the one open in the reader (Task 22 (o)), when it is
    one of the project's searchable documents; otherwise it is the same "required" error."""
    if value is None or value == "":
        if open_if_missing and (found := open_document(scope)) is not None:
            return found
        return {"error": f"document is required: use a number or name from the list: {choices_text(scope)}."}
    found = resolve_document(scope, value)
    if found is not None:
        return found
    listed = resolve_document(list(ctx.project.docs), value)
    if listed is not None:   # in the project, but not indexed (or reindexing)
        return {"error": f'Document {listed.ref} ("{listed.name}") isn\'t searchable yet.'}
    return {"error": f"Unknown document: {display_name(value)}. "
                     f"Use a number or name from the list: {choices_text(scope)}."}
