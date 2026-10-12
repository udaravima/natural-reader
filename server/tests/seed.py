"""The only place tests write documents, entries/shares and project placements.
A schema change to how documents are held touches this file, not every test.

Vocabulary is A1's: a *holder* has the content in their library, a *share*
gives someone else an entry, a *placement* files content into a project.

Rows are VERIFIED by default — they model post-A1 state, where every holding
traces back to someone who uploaded the bytes. Pass `verified=False` to seed
a legacy row as migration 012 leaves it (a pre-A1 claim nobody proved).

A placement is a project document's version (V1, migration 018): place_doc makes
a new document at v1; add_version appends to one.
"""
from __future__ import annotations


async def seed_doc(conn, doc_id, holder_id, *, file_name="f.pdf", file_type="pdf",
                   tags=(), state="registered", project_ids=(), bytes_path=None,
                   extracted_by="client", verified=True):
    """Content + (unless holder_id is None) the holder's upload entry, and
    placements in `project_ids` added by the holder (same `verified`)."""
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
        "bytes_path, extracted_by) VALUES (%s,%s,%s,1,%s,%s,%s)",
        (doc_id, file_name, file_type, state, str(bytes_path) if bytes_path else None,
         extracted_by))
    if holder_id is not None:
        await conn.execute(
            "INSERT INTO library_entries (user_id, doc_id, tags, added_via, verified) "
            "VALUES (%s,%s,%s,'upload',%s)", (holder_id, doc_id, list(tags), verified))
    for pid in project_ids:
        await place_doc(conn, pid, doc_id, holder_id, verified=verified)


async def share_doc(conn, doc_id, sharer_id, recipient_id, *, verified=True):
    await conn.execute(
        "INSERT INTO library_entries (user_id, doc_id, added_via, shared_by, verified) "
        "VALUES (%s,%s,'shared',%s,%s) ON CONFLICT DO NOTHING",
        (recipient_id, doc_id, sharer_id, verified))


async def place_doc(conn, project_id, doc_id, added_by, *, verified=True, file_name=None):
    """File content into a project as a NEW document: version 1, current
    (V1 spec §3). Named like migration 018 names a placement: `file_name`,
    else the filer's entry name, else the canonical name. Returns the
    project document's id."""
    cur = await conn.execute(
        "INSERT INTO project_docs (project_id, created_by, last_version) "
        "VALUES (%s,%s,1) RETURNING id", (project_id, added_by))
    pdoc = str((await cur.fetchone())[0])
    await conn.execute(
        "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, file_name, "
        "origin, is_current, added_by, verified) VALUES (%s,%s,1,%s,COALESCE(%s,"
        "(SELECT e.file_name FROM library_entries e WHERE e.user_id = %s AND e.doc_id = %s),"
        "(SELECT d.file_name FROM documents d WHERE d.doc_id = %s)),'upload',true,%s,%s)",
        (project_id, pdoc, doc_id, file_name, added_by, doc_id, doc_id, added_by, verified))
    return pdoc


async def add_version(conn, project_doc_id, doc_id, added_by, *, file_name=None, verified=True,
                      origin="upload", origin_version=None, note=None, current=True):
    """Append a version to a project document WITHOUT the service's rules,
    for building histories in tests. `current=True` makes it the current one.
    Named `file_name`, else the content's canonical name. Returns its number."""
    cur = await conn.execute(
        "UPDATE project_docs SET last_version = last_version + 1 WHERE id = %s "
        "RETURNING last_version, project_id", (project_doc_id,))
    n, project_id = await cur.fetchone()
    if current:
        await conn.execute("UPDATE project_documents SET is_current = false "
                           "WHERE project_doc_id = %s AND is_current", (project_doc_id,))
    await conn.execute(
        "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, file_name, "
        "note, origin, origin_version, is_current, added_by, verified) VALUES (%s,%s,%s,%s,"
        "COALESCE(%s,(SELECT d.file_name FROM documents d WHERE d.doc_id = %s)),%s,%s,%s,%s,%s,%s)",
        (project_id, project_doc_id, n, doc_id, file_name, doc_id, note, origin, origin_version,
         current, added_by, verified))
    return n


async def make_project(conn, owner_id, name="P", *, members=()):
    """A project created by `owner_id`, who is its Owner, plus `members` as
    (user_id, role) pairs. `owner_id=None` makes an ownerless project.
    Returns the project id as a string."""
    cur = await conn.execute(
        "INSERT INTO projects (created_by, name) VALUES (%s,%s) RETURNING id",
        (owner_id, name))
    pid = str((await cur.fetchone())[0])
    if owner_id is not None:
        await add_member(conn, pid, owner_id, "owner")
    for user_id, role in members:
        await add_member(conn, pid, user_id, role)
    return pid


async def add_member(conn, project_id, user_id, role="contributor"):
    """Membership with a role (A0); re-adding a member changes their role."""
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES (%s,%s,%s) "
        "ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        (project_id, user_id, role))
