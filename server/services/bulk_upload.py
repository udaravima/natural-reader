"""What a file would become in a project, for the plan route and the upload route alike (bulk upload spec, phase 2).

`decide` is the one place that answers it, so the plan can't promise what the upload won't do. It follows the order
filing from the library uses (doc_versions.file_from_library): the content is already current here, an explicit
document, an older version here, a name match, new. Only this project is ever looked at: neither route says whether
the server holds a file somewhere else.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from ..http_errors import refusal
from . import doc_storage, doc_versions, people

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
MAX_FILES_DEFAULT = 200
RESERVE_MB_DEFAULT = 1024
NOT_SUPPORTED = "Not a PDF, Markdown or text file."


def _setting(env: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(env)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = minimum - 1
    if value < minimum:
        logger.warning("%s=%r is not a whole number of at least %d; using %d", env, raw, minimum, default)
        return default
    return value


def max_files() -> int:
    """BULK_UPLOAD_MAX_FILES: the most files one plan may name."""
    return _setting("BULK_UPLOAD_MAX_FILES", MAX_FILES_DEFAULT, minimum=1)


def reserve_mb() -> int:
    """UPLOAD_MIN_FREE_MB: what the storage folder keeps free, in MB."""
    return _setting("UPLOAD_MIN_FREE_MB", RESERVE_MB_DEFAULT, minimum=0)


def free_mb() -> int:
    """Free space where uploads are stored, in whole MB."""
    folder = doc_storage.storage_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(folder).free // MIB


def clean_name(raw: str | None) -> str:
    """The name a file is filed under: its last path part, trimmed, at most 255 characters. A project is a flat list,
    so a folder in the name is never kept, and the plan and the upload must name a file the same way."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
    return name or "document"


def refusal_for(name: str, size: int, head: bytes | None) -> dict[str, str] | None:
    """Why the upload would refuse this file, judged from its name, size and first bytes; None when it wouldn't.
    The same rules as doc_storage.stage_upload. A text file that isn't UTF-8 past its first bytes is still refused
    only when it is sent."""
    if size == 0:
        return {"code": "empty_file", "message": "The file is empty."}
    file_type = doc_storage.type_from_name(name)
    cap = doc_storage.cap_mb(file_type)
    if size > cap * MIB:
        kind = "a PDF" if file_type == "pdf" else "text and Markdown"
        return {"code": "too_large", "message": f"Over the {cap} MB limit for {kind}."}
    if head is not None:
        if file_type == "pdf" and not head.startswith(b"%PDF-"):
            return {"code": "unsupported_type", "message": NOT_SUPPORTED}
        if file_type != "pdf" and b"\x00" in head:  # UTF-16, or binary named .txt
            return {"code": "unsupported_type", "message": NOT_SUPPORTED}
    return None


@dataclass(frozen=True)
class Decision:
    """`action`: new | version | ambiguous | unchanged | older_version. `document`: {id, name, version} of the
    document involved, at its current version. `version`: what a new version would be numbered, or the older
    version's number. `candidates`: every same-named document, for ambiguous."""
    action: str
    document: dict[str, Any] | None = None
    version: int | None = None
    candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)


async def _current_here(conn, project_id, sha256: str) -> str | None:
    cur = await conn.execute(  # CURRENT, verified: what this caller can see
        "SELECT project_doc_id FROM project_documents WHERE project_id = %s AND doc_id = %s "
        "AND is_current AND verified", (project_id, sha256))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def _next_version(conn, document_id) -> int:
    cur = await conn.execute("SELECT last_version FROM project_docs WHERE id = %s", (document_id,))
    return (await cur.fetchone())[0] + 1


async def _named(conn, project_id, name: str, show_email: bool) -> list[dict[str, Any]]:
    cur = await conn.execute(  # CURRENT, every match (doc_versions._same_name takes the oldest only)
        "SELECT v.project_doc_id, v.file_name, v.version, v.added_at, "
        f"{people.person_cols('u')} FROM project_documents v LEFT JOIN users u ON u.id = v.added_by "
        "WHERE v.project_id = %s AND v.is_current AND v.verified "
        "AND lower(btrim(v.file_name)) = lower(btrim(%s)) ORDER BY v.added_at, v.project_doc_id",
        (project_id, name))
    return [{"id": str(r[0]), "name": r[1], "version": r[2], "added_at": r[3],
             "added_by": people.person_from(r[4:10], show_email)} for r in await cur.fetchall()]


async def _document_here(conn, project_id, document_id) -> dict[str, Any]:
    cur = await conn.execute("SELECT 1 FROM project_docs WHERE id = %s AND project_id = %s",
                             (document_id, project_id))
    ref = await doc_versions.document_ref(conn, document_id) if await cur.fetchone() else None
    if ref is None:
        raise refusal(404, "not_found", doc_versions.DOCUMENT_NOT_FOUND)
    return ref


async def decide(conn, project_id, *, name: str, sha256: str | None, document_id=None,
                 show_email: bool = False) -> Decision:
    """What this file would become here. Reads only. Without `sha256` (a browser on plain HTTP can't hash) only the
    name is matched. With `document_id`, the caller said which document the file belongs to."""
    if sha256 is not None:
        holder = await _current_here(conn, project_id, sha256)
        if holder is not None:
            if document_id is not None and str(document_id) != holder:
                await _document_here(conn, project_id, document_id)  # "not here" before "elsewhere"
                raise doc_versions._conflict("current_elsewhere", doc_versions.CURRENT_ELSEWHERE,
                                             await doc_versions.document_ref(conn, holder))
            ref = await doc_versions.document_ref(conn, holder)
            return Decision("unchanged", ref, ref["version"])
    if document_id is not None:
        ref = await _document_here(conn, project_id, document_id)
        if sha256 is not None:
            cur = await conn.execute(  # HISTORY of that document
                "SELECT max(version) FROM project_documents WHERE project_doc_id = %s AND doc_id = %s "
                "AND verified", (document_id, sha256))
            older = (await cur.fetchone())[0]
            if older is not None:
                return Decision("older_version", ref, older)
        return Decision("version", ref, await _next_version(conn, document_id))
    if sha256 is not None:
        older = await doc_versions._older_version_of(conn, project_id, sha256)
        if older:
            return Decision("older_version", await doc_versions.document_ref(conn, older[0]), older[1])
    named = await _named(conn, project_id, name, show_email)
    if len(named) == 1:
        ref = await doc_versions.document_ref(conn, named[0]["id"])
        return Decision("version", ref, await _next_version(conn, named[0]["id"]))
    if named:
        return Decision("ambiguous", candidates=tuple(named))
    return Decision("new")


# ---------- the upload: decide under the project's upload lock, then file ----------

STORES = frozenset({"created", "version", "restored"})


@dataclass(frozen=True)
class Outcome:
    """What the upload does with a file. `result`: created | version | restored | unchanged | skipped |
    left_older | ambiguous. `document_id`: the document a version or restore files into."""
    result: str
    decision: Decision
    document_id: str | None = None

    @property
    def stores(self) -> bool:
        return self.result in STORES


def _outcome(d: Decision, *, explicit: bool, on_match: str, on_older: str) -> Outcome:
    if d.action == "unchanged":
        return Outcome("unchanged", d)
    if d.action == "older_version":
        return Outcome("restored", d, d.document["id"]) if on_older == "restore" else Outcome("left_older", d)
    if d.action == "version":
        if explicit or on_match == "version":  # document_id names the document: on_match is about name matches
            return Outcome("version", d, d.document["id"])
        return Outcome("created", d) if on_match == "separate" else Outcome("skipped", d)
    if d.action == "ambiguous":  # only "version" would have to guess between them
        return {"separate": Outcome("created", d), "skip": Outcome("skipped", d)}.get(on_match,
                                                                                    Outcome("ambiguous", d))
    return Outcome("created", d)


async def decide_and_lock(conn, user_id: str, project_id, *, name: str, sha256: str, document_id=None,
                          on_match: str = "version", on_older: str = "leave", show_email: bool = False) -> Outcome:
    """Inside the caller's transaction, before any content is written. The document to lock is known only after
    deciding, so uploads into one project first take a transaction-level advisory lock: two uploads then decide
    one after the other, each from the project as the other left it (two identical files: one version, one
    unchanged, not a 409). Lock order: this lock, the project, the document; nothing else takes this lock."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"project_upload:{project_id}",))
    await doc_versions.lock_project_key(conn, project_id)
    await doc_versions.prove_legacy_current(conn, project_id, sha256, user_id)
    d = await decide(conn, project_id, name=name, sha256=sha256, document_id=document_id, show_email=show_email)
    outcome = _outcome(d, explicit=document_id is not None, on_match=on_match, on_older=on_older)
    if outcome.document_id is not None:
        await doc_versions.lock_document(conn, project_id, outcome.document_id)
    return outcome


async def file_it(conn, user_id: str, project_id, outcome: Outcome, *, doc_id: str, name: str,
                  note: str | None) -> dict[str, Any]:
    """File content the upload just stored, as the outcome says. Returns the document as {id, name, version}."""
    if outcome.result == "created":
        document_id = await doc_versions.create_document(conn, user_id, project_id, doc_id, file_name=name)
    else:  # version, or restored: add_version records an older version's bytes as its restore, under its name
        document_id = outcome.document_id
        await doc_versions.add_version(conn, user_id, project_id, document_id, doc_id=doc_id, file_name=name,
                                       note=note)
    return await doc_versions.document_ref(conn, document_id)
