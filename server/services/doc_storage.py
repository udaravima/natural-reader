"""Content bytes on disk (A1 spec §4, §9).

An upload streams into a staging file while it is hashed; the server's
SHA-256 is the doc id. The staged file moves into place (`<dir>/<doc_id>.<ext>`)
only after the caller's INSERT has committed (spec §3, races), so a
concurrent GC can never delete a file a newer upload just wrote. The user's
file name is display-only and never part of a path.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from ..http_errors import refusal

MIB = 1024 * 1024
_EXT = {"pdf": "pdf", "text": "txt", "markdown": "md"}


def storage_dir() -> Path:
    raw = os.environ.get("DOC_STORAGE_DIR") or os.environ.get("PDF_STORAGE_DIR") or "./data/pdfs"
    return Path(raw).resolve()


def _cap_mb(file_type: str) -> int:
    if file_type == "pdf":
        return int(os.environ.get("PDF_UPLOAD_MAX_MB", "50"))
    return int(os.environ.get("TEXT_UPLOAD_MAX_MB", "10"))


def type_from_name(name: str) -> str:
    """Same rule as the SPA's detectFileType: .md/.markdown, .txt, else PDF."""
    lower = (name or "").lower()
    if lower.endswith((".md", ".markdown")):
        return "markdown"
    if lower.endswith(".txt"):
        return "text"
    return "pdf"


@dataclass
class Staged:
    path: Path
    sha256: str
    size: int
    file_type: str


def _unsupported():
    return refusal(415, "unsupported_type", "Unsupported file type.")


def _check_type(path: Path, file_type: str, head: bytes) -> None:
    if file_type == "pdf":
        if not head.startswith(b"%PDF-"):
            raise _unsupported()
        return
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise _unsupported() from None
    if "\x00" in text:  # binary disguised as text; Postgres TEXT can't hold NUL
        raise _unsupported()


async def stage_upload(upload: UploadFile, file_name: str) -> Staged:
    file_type = type_from_name(file_name)
    cap_mb = _cap_mb(file_type)
    staging = storage_dir() / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(tmp, "wb") as out:
            while chunk := await upload.read(MIB):
                size += len(chunk)
                if size > cap_mb * MIB:
                    raise refusal(413, "too_large", f"File exceeds the {cap_mb} MB limit.",
                                  limit_mb=cap_mb)
                if len(head) < 5:
                    head += chunk[: 5 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise refusal(422, "empty_file", "The file is empty.")
        _check_type(tmp, file_type, head)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return Staged(tmp, digest.hexdigest(), size, file_type)


def place(staged: Staged, doc_id: str) -> Path:
    base = storage_dir()
    final = (base / f"{doc_id}.{_EXT[staged.file_type]}").resolve()
    if not final.is_relative_to(base):  # defense in depth: doc_id is our own hex digest
        raise ValueError("storage path escaped DOC_STORAGE_DIR")
    os.replace(staged.path, final)  # atomic within one filesystem
    return final


def discard(staged: Staged) -> None:
    staged.path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(MIB):
                digest.update(block)
        return digest.hexdigest()
    except FileNotFoundError:
        return None
