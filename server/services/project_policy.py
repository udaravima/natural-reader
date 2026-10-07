"""Who may create projects, and how many (A0 §5, §12).

PROJECT_CREATION: `readers` (anyone with the reader capability, the default)
or `admins`. PROJECT_LIMIT_PER_USER: projects a person may create (counted
by `projects.created_by`); default 20, 0 = unlimited. An admin can override
it per user (`users.project_limit`, NULL = the default; 0 = unlimited).
Admins are never limited. Invalid values log a WARNING and use the default."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..http_errors import refusal

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class ProjectPolicy:
    creation: str
    limit_per_user: int


def load_project_policy(env: Mapping[str, str] = os.environ) -> ProjectPolicy:
    creation = env.get("PROJECT_CREATION", "").strip().lower() or "readers"
    if creation not in ("readers", "admins"):
        logger.warning("PROJECT_CREATION=%r is not readers or admins; using readers", creation)
        creation = "readers"
    raw = env.get("PROJECT_LIMIT_PER_USER", "").strip() or str(DEFAULT_LIMIT)
    try:
        limit = int(raw)
    except ValueError:
        limit = -1
    if limit < 0:
        logger.warning("PROJECT_LIMIT_PER_USER=%r is not a whole number >= 0; using %d",
                       raw, DEFAULT_LIMIT)
        limit = DEFAULT_LIMIT
    return ProjectPolicy(creation=creation, limit_per_user=limit)


async def check_can_create(conn, principal, policy: ProjectPolicy) -> None:
    if principal.role == "admin":
        return
    if policy.creation == "admins":
        raise refusal(403, "project_creation_restricted", "Only admins can create projects here.")
    # Locking the creator's row serializes one person's concurrent creates,
    # so two at once can't both slip under the limit.
    cur = await conn.execute(
        "SELECT project_limit FROM users WHERE id = %s FOR UPDATE", (principal.user_id,))
    row = await cur.fetchone()
    limit = row[0] if row is not None and row[0] is not None else policy.limit_per_user
    if limit == 0:
        return
    cur = await conn.execute("SELECT count(*) FROM projects WHERE created_by = %s",
                             (principal.user_id,))
    if (await cur.fetchone())[0] >= limit:
        logger.warning("project limit reached: user %s, limit %d", principal.user_id, limit)
        raise refusal(429, "project_limit",
                      f"You've reached your project limit ({limit}). Ask an admin to raise it.",
                      limit=limit)
