"""Who holds content (A1 spec §3, §5).

Content (`documents`) belongs to nobody. A user holds it through a library
entry; a project holds it through a placement. This module owns the writes
that change who holds content, the sole-holder rule for content-changing ops,
and garbage collection. Routes never DELETE FROM documents themselves.

GC rule (spec §3): every path that removes entries or placements collects the
affected doc_ids FIRST and calls gc_content_if_orphaned for each AFTERWARDS —
an SQL ON DELETE CASCADE never runs this code.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..audit import audit

logger = logging.getLogger(__name__)


async def add_entry(conn, user_id, doc_id, *, via, verified: bool, shared_by=None,
                    file_name=None, tags=None) -> str:
    """Give `user_id` an entry for `doc_id`. 'created', 'upgraded' (a shared
    entry became an upload entry — the user just proved possession),
    'replaced' (a verified share took over an unverified legacy one) or
    'exists'. An upload entry is never downgraded to shared (spec §5).

    `verified` (migration 012) must be passed explicitly: True only when this
    call is backed by bytes — the multipart upload path, or a share made by a
    holder of a verified upload entry. A verified upload also verifies the
    user's existing entry and the shares and placements they made for this
    doc (`_verify_holdings`). A verified share replaces a recipient's
    UNVERIFIED legacy share (else it would silently grant nothing); it never
    touches an upload entry or a verified share."""
    cur = await conn.execute(
        "INSERT INTO library_entries (user_id, doc_id, file_name, tags, added_via, shared_by, "
        "verified) VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (user_id, doc_id) DO NOTHING RETURNING 1",
        (user_id, doc_id, file_name, sorted(set(tags or [])), via, shared_by, verified))
    if await cur.fetchone():
        outcome = "created"
    elif via != "upload":
        outcome = "exists"
        if verified:
            cur = await conn.execute(
                "UPDATE library_entries SET shared_by = %s, verified = true, "
                "file_name = COALESCE(file_name, %s) WHERE user_id = %s AND doc_id = %s "
                "AND added_via = 'shared' AND NOT verified",
                (shared_by, file_name, user_id, doc_id))
            if cur.rowcount == 1:
                outcome = "replaced"
    else:
        cur = await conn.execute(
            "UPDATE library_entries SET added_via = 'upload', shared_by = NULL, verified = %s "
            "WHERE user_id = %s AND doc_id = %s AND added_via = 'shared'",
            (verified, user_id, doc_id))
        outcome = "upgraded" if cur.rowcount == 1 else "exists"
        if tags:
            await conn.execute(
                "UPDATE library_entries SET tags = %s WHERE user_id = %s AND doc_id = %s",
                (sorted(set(tags)), user_id, doc_id))
    if via == "upload" and verified:
        await _verify_holdings(conn, user_id, doc_id)
    return outcome


async def _verify_holdings(conn, user_id, doc_id) -> None:
    """`user_id` just uploaded `doc_id`'s bytes: their entry, the shares they
    created and the placements they added are proved (migration 012)."""
    entry = await conn.execute(
        "UPDATE library_entries SET verified = true WHERE user_id = %s AND doc_id = %s "
        "AND NOT verified", (user_id, doc_id))
    shares = await conn.execute(
        "UPDATE library_entries SET verified = true WHERE doc_id = %s AND added_via = 'shared' "
        "AND shared_by = %s AND NOT verified", (doc_id, user_id))
    placements = await conn.execute(
        "UPDATE project_documents SET verified = true WHERE doc_id = %s AND added_by = %s "
        "AND NOT verified", (doc_id, user_id))
    if entry.rowcount or shares.rowcount or placements.rowcount:
        audit("entry.verified", user=user_id, doc=doc_id,
              shares=shares.rowcount, placements=placements.rowcount)


async def remove_entry(conn, user_id, doc_id) -> bool:
    cur = await conn.execute(
        "DELETE FROM library_entries WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
    return cur.rowcount == 1


async def revoke_share(conn, sharer_id, recipient_id, doc_id) -> bool:
    """Removes only a shared entry this sharer created (spec §5)."""
    cur = await conn.execute(
        "DELETE FROM library_entries WHERE user_id = %s AND doc_id = %s "
        "AND added_via = 'shared' AND shared_by = %s", (recipient_id, doc_id, sharer_id))
    return cur.rowcount == 1


async def holds_entry(conn, user_id, doc_id) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
    return await cur.fetchone() is not None


async def holds_upload(conn, user_id, doc_id) -> bool:
    """A VERIFIED upload entry: the user sent this server the bytes."""
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE user_id = %s AND doc_id = %s "
        "AND added_via = 'upload' AND verified", (user_id, doc_id))
    return await cur.fetchone() is not None


async def content_ops_refusal(conn, doc_id, user_id, *, is_admin) -> str | None:
    """None when `user_id` may change the content itself (convert, delete
    converted markdown, re-index): an admin, or the sole holder — exactly one
    entry, theirs, and no placements. Otherwise the reason for the 409."""
    if is_admin:
        return None
    cur = await conn.execute(
        "SELECT count(*) FILTER (WHERE user_id <> %s), count(*) FILTER (WHERE user_id = %s) "
        "FROM library_entries WHERE doc_id = %s", (user_id, user_id, doc_id))
    others, mine = await cur.fetchone()
    if others or not mine:
        return "other_holders"
    cur = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM project_documents WHERE doc_id = %s)", (doc_id,))
    return "in_project" if (await cur.fetchone())[0] else None


async def docs_referenced_by_user(conn, user_id) -> list[str]:
    """Docs whose last reference may vanish when this user is deleted: their
    entries, plus placements in projects they own (projects cascade on user
    delete until A0 ends that). Sorted: GC locks each content row, and every
    multi-doc GC path taking them in the same order can't deadlock."""
    cur = await conn.execute(
        "SELECT doc_id FROM library_entries WHERE user_id = %s "
        "UNION SELECT pd.doc_id FROM project_documents pd "
        "JOIN projects p ON p.id = pd.project_id WHERE p.owner_user_id = %s "
        "ORDER BY 1", (user_id, user_id))
    return [r[0] for r in await cur.fetchall()]


async def gc_content_if_orphaned(conn, doc_id, *, trigger: str) -> bool:
    """Delete content nobody holds: row (cascading chunks and pages) and bytes.
    FOR UPDATE on the row blocks a concurrent entry insert (its FK check takes
    KEY SHARE) until we commit. The bytes file is unlinked while that lock is
    held, before commit; uploads move their file into place only after their
    own INSERT succeeds, so we can never delete a newer upload's file."""
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT bytes_path FROM documents WHERE doc_id = %s FOR UPDATE", (doc_id,))
        row = await cur.fetchone()
        if row is None:
            return False
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM library_entries WHERE doc_id = %s) "
            "OR EXISTS (SELECT 1 FROM project_documents WHERE doc_id = %s)", (doc_id, doc_id))
        if (await cur.fetchone())[0]:
            return False
        if row[0]:
            try:
                Path(row[0]).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not remove bytes for doc %s: %s", doc_id, e)
        await conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
    audit("content.gc", doc=doc_id, trigger=trigger)
    return True


async def sweep_orphans(conn) -> int:
    """Startup safety net (spec §3): GC any content with no entry and no
    placement — catches a future code path that removed references without
    calling gc_content_if_orphaned."""
    cur = await conn.execute(
        "SELECT d.doc_id FROM documents d "
        "WHERE NOT EXISTS (SELECT 1 FROM library_entries e WHERE e.doc_id = d.doc_id) "
        "AND NOT EXISTS (SELECT 1 FROM project_documents pd WHERE pd.doc_id = d.doc_id)")
    removed = 0
    for (doc_id,) in await cur.fetchall():
        if await gc_content_if_orphaned(conn, doc_id, trigger="startup_sweep"):
            removed += 1
    if removed:
        logger.warning("Startup sweep removed %d orphaned documents", removed)
    return removed


async def recover_states(conn) -> None:
    """A crash mid-job leaves docs mid-state; make them resumable (spec §4)."""
    await conn.execute(
        "UPDATE documents SET state = 'stored', updated_at = now() WHERE state = 'extracting'")
    await conn.execute(
        "UPDATE documents SET state = 'extracted', updated_at = now() WHERE state = 'indexing'")
