"""Audit trail (A1 spec §10). One line per change to who holds what: entries,
shares, placements, content GC. IDs only — never emails, names, file names or
search text (no personal data at INFO or above). Written to server.log and to
its own rotating file, LOG_AUDIT_FILE (default <LOG_DIR>/audit.log)."""
from __future__ import annotations

import logging

_logger = logging.getLogger("server.audit")


def audit(event: str, **fields: object) -> None:
    _logger.info("%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))
