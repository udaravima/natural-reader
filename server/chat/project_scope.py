"""A chat's project scope (C2.1 spec §5.1): the project's current documents,
as the document tools see them this turn.

Access is the route's job (require_project_role, re-checked every turn);
the documents come only from doc_versions.list_project_documents (current
versions), so "a project chat sees only current versions" has the one
definition V1 gave it. Each document gets a short `ref` (its position in
that list, from 1) and a name unique within the scope, so a tool call or a
citation can name it without ambiguity.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..auth.authz import visible_projects_params, visible_projects_where
from ..services import doc_pipeline, doc_versions
from ..services.doc_search import ReadableDoc
from .prompt import NAME_CAP, display_name


@dataclass(frozen=True)
class ScopeDoc:
    ref: int              # the document's number in this scope, from 1
    doc: ReadableDoc      # its state is the one after doc_pipeline.ensure_current
    name: str             # display_name, unique within the scope: final, shown and saved exactly as it is
    open_in_reader: bool  # the reader has this very document open

    def __post_init__(self) -> None:
        # The name goes to the model as it is (the map, the first line, tool results) and into the scope note that
        # citations are matched against, so it must already be one inert line within the cap. A name
        # load_project_scope made already is (display_name leaves it alone); any other is made so here, once.
        object.__setattr__(self, "name", display_name(self.name))


@dataclass(frozen=True)
class ProjectScope:
    project_id: str
    name: str             # the project's own name, as stored (make it inert before showing it)
    docs: tuple[ScopeDoc, ...]

    @property
    def searchable(self) -> tuple[ScopeDoc, ...]:
        return tuple(d for d in self.docs if d.doc.state == "indexed")


def _numbered(name: str, n: int) -> str:
    """`name` with " (n)", within NAME_CAP: a name too long to take the number is cut first and ends in "…", so the
    number is part of the final name and display_name never cuts it off again (final review M1: two long names
    that differ only past the cap were both shown, and so cited, as the first)."""
    suffix = f" ({n})"
    if len(name) + len(suffix) <= NAME_CAP:
        return name + suffix
    return name[:NAME_CAP - len(suffix) - 1].rstrip() + "…" + suffix


def unique_names(names: list[str]) -> list[str]:
    """Each name (already through display_name), made unique (case-insensitively, as names are looked up):
    a repeat gets " (2)", " (3)" in order, skipping any number another
    document already has as its real name."""
    taken = {n.casefold() for n in names}
    seen: set[str] = set()
    out = []
    for name in names:
        key = name.casefold()
        if key in seen:
            n = 2
            while _numbered(name, n).casefold() in taken:
                n += 1
            name = _numbered(name, n)
            taken.add(name.casefold())
        seen.add(key)
        out.append(name)
    return out


async def load_project_scope(conn, user_id: str, project_id: str, *,
                             open_doc_id: str | None) -> ProjectScope:
    """The project's current documents, in list_project_documents order.
    Raises LookupError unless `user_id` is a member: the route checked, and
    this keeps a scope from ever being built for someone who isn't."""
    cur = await conn.execute(
        f"SELECT p.name FROM projects p WHERE p.id = %s AND {visible_projects_where('p')}",
        [project_id, *visible_projects_params(user_id)])
    row = await cur.fetchone()
    if row is None:
        raise LookupError("project not visible")
    listed = await doc_versions.list_project_documents(conn, project_id, show_email=False)
    cur = await conn.execute(
        "SELECT doc_id, state, page_count, embedding_profile, embedding_model FROM documents "
        "WHERE doc_id = ANY(%s)", ([d["doc_id"] for d in listed],))
    content = {r[0]: r for r in await cur.fetchall()}
    names = unique_names([display_name(d["name"]) for d in listed])
    docs = []
    for ref, (d, name) in enumerate(zip(listed, names), start=1):
        _, state, page_count, profile, model = content[d["doc_id"]]
        # First use under a new embedding profile starts a background rebuild,
        # as for the open document (orchestrator._open_doc).
        doc = doc_pipeline.ensure_current(ReadableDoc(d["doc_id"], d["name"], state, page_count, profile, model))
        docs.append(ScopeDoc(ref, doc, name, d["doc_id"] == open_doc_id))
    return ProjectScope(str(project_id), row[0], tuple(docs))
