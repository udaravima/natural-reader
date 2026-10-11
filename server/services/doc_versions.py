"""Versions of a project's documents (V1 spec
docs/superpowers/specs/2026-10-12-document-versions-design.md).

A project document (`project_docs`) is "a document in a project"; each of its
versions is a `project_documents` row pointing at content. This module owns
every write that changes a history and the queries that read one; routes
never write project_documents themselves.

Two query groups (spec §3.4), named on every query here:
  HISTORY  every verified version — reading old versions, versions_in;
  CURRENT  the verified current version — listings and counts.
A query in the wrong group fails predictably: HISTORY narrowed to current
breaks reading old versions; CURRENT left unfiltered lists duplicates.

Lock order (spec §4.4): a document's project_docs row FOR UPDATE first, then
content. GC locks content only, so no cycle is possible.
"""
from __future__ import annotations

from typing import Any

from ..auth.authz import visible_projects_params, visible_projects_where
from ..http_errors import refusal
from . import people

DOCUMENT_NOT_FOUND = "This document isn't in the project, or it was removed."


def display_name_sql(doc_alias: str, entry_alias: str) -> str:
    """The name the caller sees for content (spec §7), in order: their entry's
    name — an entry with no name of its own means the canonical name, the
    entry holder's own upload name; else (no entry) a visible verified
    version's file_name, the current one first, then the newest; else the
    canonical name. Needs `LEFT JOIN library_entries <entry_alias>` on the
    caller. Bind `display_name_params(user_id)`."""
    return (
        f"COALESCE({entry_alias}.file_name, CASE WHEN {entry_alias}.user_id IS NULL THEN ("
        "SELECT _nv.file_name FROM project_documents _nv "  # HISTORY
        "JOIN projects _np ON _np.id = _nv.project_id "
        f"WHERE _nv.doc_id = {doc_alias}.doc_id AND _nv.verified "
        f"AND {visible_projects_where('_np')} "
        "ORDER BY _nv.is_current DESC, _nv.added_at DESC, _nv.project_id LIMIT 1) END, "
        f"{doc_alias}.file_name)"
    )


def display_name_params(user_id: str) -> list[str]:
    return visible_projects_params(user_id)


def versions_in_sql(doc_alias: str, *, only_old: bool = False) -> str:
    """JSON array, one item per visible project document whose verified
    history holds `<doc_alias>`'s content (spec §5.2), sorted by project name:
    {project: {id, name}, document_id, version (its highest number there),
    current_version, current_doc_id, current_name, is_current}. HISTORY.
    `only_old`: just the documents where it isn't current (the Library's
    `newer_in`). Only projects the caller is a member of are ever named.
    Bind `versions_in_params(user_id)`."""
    having = "HAVING NOT bool_or(_vv.is_current) " if only_old else ""
    return (
        "COALESCE((SELECT json_agg(json_build_object("
        "'project', json_build_object('id', _x.pid, 'name', _x.pname), "
        "'document_id', _x.pdoc, 'version', _x.version, 'current_version', _x.cur_version, "
        "'current_doc_id', _x.cur_doc, 'current_name', _x.cur_name, 'is_current', _x.is_current) "
        "ORDER BY _x.pname, _x.pid, _x.pdoc) FROM ("
        "SELECT _vp.id AS pid, _vp.name AS pname, _vv.project_doc_id AS pdoc, "
        "max(_vv.version) AS version, bool_or(_vv.is_current) AS is_current, "
        "_vc.version AS cur_version, _vc.doc_id AS cur_doc, _vc.file_name AS cur_name "
        "FROM project_documents _vv JOIN projects _vp ON _vp.id = _vv.project_id "  # HISTORY
        "JOIN project_documents _vc ON _vc.project_doc_id = _vv.project_doc_id "
        "AND _vc.is_current AND _vc.verified "
        f"WHERE _vv.doc_id = {doc_alias}.doc_id AND _vv.verified "
        f"AND {visible_projects_where('_vp')} "
        "GROUP BY _vp.id, _vp.name, _vv.project_doc_id, _vc.version, _vc.doc_id, _vc.file_name "
        f"{having}) _x), '[]'::json)"
    )


def versions_in_params(user_id: str) -> list[str]:
    return visible_projects_params(user_id)


async def versions_in(conn, doc_id: str, user_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {versions_in_sql('d')} FROM documents d WHERE d.doc_id = %s",
        [*versions_in_params(user_id), doc_id])
    row = await cur.fetchone()
    return list(row[0]) if row else []


def old_version_of(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The banner rule (spec §7): the first item when the content is an old
    version in at least one visible project and current in none; else None."""
    if not items or any(i["is_current"] for i in items):
        return None
    return items[0]


_DOCUMENT_SELECT = (
    "SELECT v.project_doc_id, v.file_name, v.version, v.doc_id, d.state, v.added_at, "
    f"{people.person_cols('u')}, "
    "(SELECT count(*) FROM project_documents _c "  # HISTORY: how many versions it has
    "WHERE _c.project_doc_id = v.project_doc_id AND _c.verified) "
    "FROM project_documents v JOIN documents d ON d.doc_id = v.doc_id "
    "LEFT JOIN users u ON u.id = v.added_by "
    "WHERE v.project_id = %s AND v.is_current AND v.verified "  # CURRENT
)


def _document(r, show_email: bool) -> dict[str, Any]:
    return {"id": str(r[0]), "name": r[1], "version": r[2], "doc_id": r[3], "state": r[4],
            "updated_at": r[5], "updated_by": people.person_from(r[6:12], show_email),
            "versions_count": r[12]}


async def list_project_documents(conn, project_id, *, show_email: bool) -> list[dict[str, Any]]:
    """A project's documents (spec §5.1): one row per document — its verified
    current version — sorted by name. CURRENT. C2.1's project scope reuses
    this, so "project chat sees only current versions" has one definition."""
    cur = await conn.execute(_DOCUMENT_SELECT + "ORDER BY lower(v.file_name), v.project_doc_id",
                             (project_id,))
    return [_document(r, show_email) for r in await cur.fetchall()]


async def document_row(conn, project_id, document_id, *, show_email: bool) -> dict | None:
    cur = await conn.execute(_DOCUMENT_SELECT + "AND v.project_doc_id = %s",
                             (project_id, document_id))
    row = await cur.fetchone()
    return _document(row, show_email) if row else None


async def list_versions(conn, project_id, document_id, *, show_email: bool) -> list[dict]:
    """A document's verified versions, newest first (spec §5.1). HISTORY.
    404 unless it is a visible document of this project (a verified current
    version: a document whose current version is unverified is invisible, as
    an unverified placement is)."""
    cur = await conn.execute(
        "SELECT v.version, v.doc_id, v.file_name, v.note, v.origin, v.origin_version, "
        f"v.added_at, v.is_current, d.state, {people.person_cols('u')} "
        "FROM project_documents v JOIN documents d ON d.doc_id = v.doc_id "
        "LEFT JOIN users u ON u.id = v.added_by "
        "WHERE v.project_doc_id = %s AND v.project_id = %s AND v.verified "  # HISTORY
        "ORDER BY v.version DESC", (document_id, project_id))
    rows = await cur.fetchall()
    if not any(r[7] for r in rows):
        raise refusal(404, "not_found", DOCUMENT_NOT_FOUND)
    return [{"version": r[0], "doc_id": r[1], "file_name": r[2], "note": r[3], "origin": r[4],
             "origin_version": r[5], "added_at": r[6], "is_current": r[7], "state": r[8],
             "added_by": people.person_from(r[9:15], show_email)} for r in rows]


async def document_ref(conn, document_id) -> dict[str, Any] | None:
    """`{id, name, version}` of the document's verified current version: how
    a 409 names the document involved (spec §5.3). CURRENT."""
    cur = await conn.execute(
        "SELECT file_name, version FROM project_documents "  # CURRENT
        "WHERE project_doc_id = %s AND is_current AND verified", (document_id,))
    row = await cur.fetchone()
    return {"id": str(document_id), "name": row[0], "version": row[1]} if row else None


async def lock_document(conn, project_id, document_id) -> int:
    """Lock the document's row — always the first lock (spec §4.4) — and
    return its last_version. 404 unless it is a visible document of this
    project."""
    cur = await conn.execute(
        "SELECT last_version FROM project_docs WHERE id = %s AND project_id = %s FOR UPDATE",
        (document_id, project_id))
    row = await cur.fetchone()
    if row is None or await document_ref(conn, document_id) is None:
        raise refusal(404, "not_found", DOCUMENT_NOT_FOUND)
    return row[0]
