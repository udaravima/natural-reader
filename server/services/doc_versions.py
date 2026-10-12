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

from psycopg import errors as pg_errors

from ..audit import audit
from ..auth.authz import visible_projects_params, visible_projects_where
from ..http_errors import refusal
from . import doc_content, people, project_events

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


NOTE_MAX = 500
VERSION_NOT_FOUND = "That version doesn't exist."
ALREADY_CURRENT = "This file is already the current version."
CURRENT_ELSEWHERE = ("This file is already the current version of another document in this "
                     "project. Merge the two instead.")
IS_CURRENT = "This is the current version. Remove the document instead."
MERGE_SELF = "A document can't be merged into itself."


def _conflict(code: str, message: str, document: dict | None, **extra):
    """A 409 that names the document involved (spec §5.3)."""
    return refusal(409, code, message, document=document, **extra)


def clean_note(note: str | None) -> str | None:
    """Trimmed; empty → None; over NOTE_MAX characters → 422 (spec §4.4)."""
    if note is None or not note.strip():
        return None
    note = note.strip()
    if len(note) > NOTE_MAX:
        raise refusal(422, "note_too_long", f"A note can be at most {NOTE_MAX} characters.")
    return note


async def filed_name(conn, doc_id: str, user_id: str) -> str | None:
    """The name the filer sees for content (their entry's, else the
    content's): what a version filed from their library is called."""
    cur = await conn.execute(
        "SELECT COALESCE(e.file_name, d.file_name) FROM library_entries e "
        "JOIN documents d ON d.doc_id = e.doc_id WHERE e.user_id = %s AND e.doc_id = %s",
        (user_id, doc_id))
    row = await cur.fetchone()
    return row[0] if row else None


async def verify_legacy_current(conn, project_id, doc_id: str, user_id: str) -> str | None:
    """Spec §4.2 step 0: an UNVERIFIED current version holding this content is
    proved by this user (who sent the bytes or holds a verified upload), as
    the pre-V1 filing route did. The duplicate-guard index ignores `verified`,
    so without this a new current row for the same content would violate it.
    Returns the document it made visible, or None."""
    cur = await conn.execute(
        "UPDATE project_documents SET verified = true, added_by = %s "
        "WHERE project_id = %s AND doc_id = %s AND is_current AND NOT verified "  # CURRENT, verified or not
        "RETURNING project_doc_id", (user_id, project_id, doc_id))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def _same_bytes(conn, project_id, document_id, doc_id: str) -> tuple[str | None, Any]:
    """Spec §4.3, in its order: ('already_current', None), ('current_elsewhere',
    other document id), ('older', highest version k), or (None, None)."""
    cur = await conn.execute(  # HISTORY in this project
        "SELECT project_doc_id, version, is_current FROM project_documents "
        "WHERE project_id = %s AND doc_id = %s AND verified", (project_id, doc_id))
    rows = [(str(r[0]), r[1], r[2]) for r in await cur.fetchall()]
    did = str(document_id)
    if any(p == did and current for p, _v, current in rows):
        return "already_current", None
    other = next((p for p, _v, current in rows if p != did and current), None)
    if other:
        return "current_elsewhere", other
    older = [v for p, v, _c in rows if p == did]
    return ("older", max(older)) if older else (None, None)


async def _version_row(conn, document_id, version: int):
    cur = await conn.execute(  # HISTORY
        "SELECT doc_id, file_name, is_current FROM project_documents "
        "WHERE project_doc_id = %s AND version = %s AND verified", (document_id, version))
    return await cur.fetchone()


async def _current_holder(conn, project_id, doc_id: str) -> str | None:
    cur = await conn.execute(  # CURRENT
        "SELECT project_doc_id FROM project_documents "
        "WHERE project_id = %s AND doc_id = %s AND is_current", (project_id, doc_id))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def _lost_race(conn, project_id, doc_id: str, document_id=None):
    """The 409 for a unique-index violation (spec §4.4's last net): names the
    document now holding the content as current. If that one can't be named
    (an unverified legacy row is invisible), fall back to the document being
    changed, so the body's `document` is not null when there is one."""
    other = await _current_holder(conn, project_id, doc_id)
    ref = await document_ref(conn, other) if other else None
    if ref is None and document_id is not None:
        ref = await document_ref(conn, document_id)
    return _conflict("current_elsewhere", CURRENT_ELSEWHERE, ref)


async def _append(conn, project_id, document_id, *, doc_id, file_name, note, origin,
                  origin_version, added_by) -> int:
    """Make `doc_id` the document's new current version, numbered
    last_version + 1. The caller holds the document's lock and has checked
    §4.3. The savepoint makes the unique indexes spec §4.4's last net: a
    concurrent action that made this content current elsewhere first becomes
    a 409, never a 500."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE project_docs SET last_version = last_version + 1 WHERE id = %s "
                "RETURNING last_version", (document_id,))
            n = (await cur.fetchone())[0]
            await conn.execute("UPDATE project_documents SET is_current = false "
                               "WHERE project_doc_id = %s AND is_current", (document_id,))
            await conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, note, origin, origin_version, is_current, added_by, verified) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,true)",
                (project_id, document_id, n, doc_id, file_name, note, origin, origin_version,
                 added_by))
    except pg_errors.UniqueViolation:
        raise await _lost_race(conn, project_id, doc_id, document_id)
    return n


async def create_document(conn, user_id: str, project_id, doc_id: str, *, file_name: str) -> str:
    """A new project document with `doc_id` as its v1 (spec §4.2 step 4).
    Returns its id. Content already current here (a concurrent filing) → 409
    current_elsewhere."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO project_docs (project_id, created_by, last_version) "
                "VALUES (%s,%s,1) RETURNING id", (project_id, user_id))
            pdoc = str((await cur.fetchone())[0])
            await conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,1,%s,%s,'upload',true,%s,true)",
                (project_id, pdoc, doc_id, file_name, user_id))
    except pg_errors.UniqueViolation:
        raise await _lost_race(conn, project_id, doc_id)
    audit("placement.added", project=project_id, doc=doc_id, document=pdoc, by=user_id)
    await project_events.record(conn, project_id, user_id, "document.added", doc_id=doc_id,
                                details={"name": file_name})
    return pdoc


async def add_version(conn, user_id: str, project_id, document_id, *, doc_id: str,
                      file_name: str, note: str | None = None) -> dict[str, Any]:
    """Append `doc_id` as the document's new current version (spec §4.1,
    §4.3). The current version's bytes → 409 already_current; an older
    version's → recorded as a restore of it, under its name; another
    document's current bytes here → 409 current_elsewhere. The caller proved
    possession (bytes sent, or a verified upload entry)."""
    note = clean_note(note)
    await lock_document(conn, project_id, document_id)
    await verify_legacy_current(conn, project_id, doc_id, user_id)
    kind, value = await _same_bytes(conn, project_id, document_id, doc_id)
    if kind == "already_current":
        raise _conflict("already_current", ALREADY_CURRENT, await document_ref(conn, document_id))
    if kind == "current_elsewhere":
        raise _conflict("current_elsewhere", CURRENT_ELSEWHERE, await document_ref(conn, value))
    restored_from = value if kind == "older" else None
    if restored_from is not None:
        file_name = (await _version_row(conn, document_id, restored_from))[1]
    n = await _append(conn, project_id, document_id, doc_id=doc_id, file_name=file_name,
                      note=note, origin="restore" if restored_from else "upload",
                      origin_version=restored_from, added_by=user_id)
    details: dict[str, Any] = {"name": file_name, "version": n}
    if restored_from is not None:
        details["from_version"] = restored_from
    if note:
        details["note"] = note
    kind_name = "document.version_restored" if restored_from else "document.version_added"
    await project_events.record(conn, project_id, user_id, kind_name, doc_id=doc_id,
                                details=details)
    audit("version.restored" if restored_from else "version.added", project=project_id,
          document=document_id, version=n, doc=doc_id, by=user_id)
    return {"version": n, "doc_id": doc_id, "restored_from": restored_from}


async def restore_version(conn, user_id: str, project_id, document_id,
                          version: int) -> dict[str, Any]:
    """Append a copy of version `version` — its content and name — as the new
    current version (spec §4.1: restore never moves a pointer back)."""
    await lock_document(conn, project_id, document_id)
    src = await _version_row(conn, document_id, version)
    if src is None:
        raise refusal(404, "not_found", VERSION_NOT_FOUND)
    doc_id, file_name, is_current = src
    if is_current:
        raise _conflict("already_current", ALREADY_CURRENT, await document_ref(conn, document_id))
    kind, value = await _same_bytes(conn, project_id, document_id, doc_id)
    if kind == "already_current":  # a later version already holds these bytes (spec §4.3)
        raise _conflict("already_current", ALREADY_CURRENT, await document_ref(conn, document_id))
    if kind == "current_elsewhere":
        raise _conflict("current_elsewhere", CURRENT_ELSEWHERE, await document_ref(conn, value))
    n = await _append(conn, project_id, document_id, doc_id=doc_id, file_name=file_name,
                      note=None, origin="restore", origin_version=version, added_by=user_id)
    await project_events.record(conn, project_id, user_id, "document.version_restored",
                                doc_id=doc_id, details={"name": file_name, "version": n,
                                                        "from_version": version})
    audit("version.restored", project=project_id, document=document_id, version=n,
          doc=doc_id, by=user_id)
    return {"version": n, "doc_id": doc_id, "restored_from": version}


async def delete_version(conn, user_id: str, project_id, document_id, version: int) -> None:
    """Delete one old version (spec §4.1), then GC its content if nothing
    else references it. The current version → 409 is_current."""
    await lock_document(conn, project_id, document_id)
    src = await _version_row(conn, document_id, version)
    if src is None:
        raise refusal(404, "not_found", VERSION_NOT_FOUND)
    doc_id, _name, is_current = src
    ref = await document_ref(conn, document_id)
    if is_current:
        raise _conflict("is_current", IS_CURRENT, ref)
    await conn.execute("DELETE FROM project_documents WHERE project_doc_id = %s AND version = %s",
                       (document_id, version))
    await project_events.record(conn, project_id, user_id, "document.version_deleted",
                                doc_id=doc_id, details={"name": ref["name"], "version": version})
    audit("version.deleted", project=project_id, document=document_id, version=version,
          doc=doc_id, by=user_id)
    await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="version_deleted")


async def merge_documents(conn, user_id: str, project_id, into_id, from_id) -> None:
    """Absorb document `from_id` into `into_id` (spec §4.1): every version of
    `from_id` moves, in its own order, numbered from into's last_version + 1,
    origin 'merge' with its old number; from's current becomes into's
    current; from's row goes. One-way. Both rows are locked in id order, so
    two crossed merges can't deadlock: the loser finds its document gone."""
    ids = sorted({str(into_id), str(from_id)})
    cur = await conn.execute(
        "SELECT id, last_version FROM project_docs WHERE id = ANY(%s::uuid[]) AND project_id = %s "
        "ORDER BY id FOR UPDATE", (ids, project_id))
    last = {str(r[0]): r[1] for r in await cur.fetchall()}
    into_ref, from_ref = await document_ref(conn, into_id), await document_ref(conn, from_id)
    if len(last) != len(ids) or into_ref is None or from_ref is None:
        raise refusal(404, "not_found", DOCUMENT_NOT_FOUND)  # "not yours" = "not there" (spec §5.3)
    if len(ids) == 1:
        raise _conflict("merge_self", MERGE_SELF, into_ref)
    cur = await conn.execute(  # HISTORY, verified or not: everything moves
        "SELECT version, verified FROM project_documents WHERE project_doc_id = %s "
        "ORDER BY version", (from_id,))
    moving = await cur.fetchall()
    start = last[str(into_id)]
    async with conn.transaction():
        await conn.execute("UPDATE project_documents SET is_current = false "
                           "WHERE project_doc_id = %s AND is_current", (into_id,))
        for i, (old, _verified) in enumerate(moving, start=1):
            await conn.execute(
                "UPDATE project_documents SET project_doc_id = %s, version = %s, origin = 'merge', "
                "origin_version = %s WHERE project_doc_id = %s AND version = %s",
                (into_id, start + i, old, from_id, old))
        await conn.execute("UPDATE project_docs SET last_version = %s WHERE id = %s",
                           (start + len(moving), into_id))
        await conn.execute("DELETE FROM project_docs WHERE id = %s", (from_id,))
    cur = await conn.execute("SELECT doc_id FROM project_documents "  # CURRENT
                             "WHERE project_doc_id = %s AND is_current", (into_id,))
    current_doc = (await cur.fetchone())[0]
    moved_verified = sum(1 for _v, verified in moving if verified)
    await project_events.record(
        conn, project_id, user_id, "document.merged", doc_id=current_doc,
        details={"from_name": from_ref["name"], "into_name": into_ref["name"],
                 "versions": moved_verified})
    audit("document.merged", project=project_id, into=into_id, absorbed=from_id,
          versions=moved_verified, by=user_id)


async def remove_document(conn, user_id: str, project_id, document_id) -> None:
    """Remove a document and its whole history (spec §4.1). Every version's
    content is collected FIRST and GC'd after: a cascade never runs GC."""
    await lock_document(conn, project_id, document_id)
    ref = await document_ref(conn, document_id)
    cur = await conn.execute(  # HISTORY, verified or not: everything the delete drops
        "SELECT DISTINCT doc_id FROM project_documents WHERE project_doc_id = %s ORDER BY 1",
        (document_id,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    cur = await conn.execute(  # CURRENT, with how many verified versions go (HISTORY)
        "SELECT doc_id, (SELECT count(*) FROM project_documents _c "
        "WHERE _c.project_doc_id = %s AND _c.verified) FROM project_documents "
        "WHERE project_doc_id = %s AND is_current", (document_id, document_id))
    current_doc, versions = await cur.fetchone()
    await conn.execute("DELETE FROM project_docs WHERE id = %s", (document_id,))
    await project_events.record(conn, project_id, user_id, "document.removed",
                                doc_id=current_doc,
                                details={"name": ref["name"], "versions": versions})
    audit("placement.removed", project=project_id, document=document_id, doc=current_doc,
          versions=versions, by=user_id)
    for d in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, d, trigger="placement_removed")
