"""The only place tests write documents, entries/shares and project placements.
A schema change to how documents are held touches this file, not every test.

Vocabulary is A1's: a *holder* has the content in their library, a *share*
gives someone else an entry, a *placement* files content into a project.
"""
from __future__ import annotations


async def seed_doc(conn, doc_id, holder_id, *, file_name="f.pdf", file_type="pdf",
                   tags=(), state="registered", project_ids=(), bytes_path=None,
                   extracted_by="client"):
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, tags, "
        "state, pdf_path) VALUES (%s,%s,%s,1,%s,%s,%s,%s)",
        (doc_id, file_name, file_type, holder_id, list(tags), state,
         str(bytes_path) if bytes_path else None))
    for pid in project_ids:
        await place_doc(conn, pid, doc_id, holder_id)


async def share_doc(conn, doc_id, sharer_id, recipient_id):
    await conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)",
        (doc_id, recipient_id))


async def place_doc(conn, project_id, doc_id, added_by):
    await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)",
        (project_id, doc_id))
