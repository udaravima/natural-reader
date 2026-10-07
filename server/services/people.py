"""People directory (A0 §4): how a person is labelled everywhere, and who a
caller may find when adding someone to a project or sharing a document.

USER_DIRECTORY_MODE: `exact` (full email only, the default), `domain`
(type-ahead within the caller's email domain, only when it is listed in
USER_DIRECTORY_DOMAINS), `open` (type-ahead over everyone). An exact email
works in every mode and finds active and pending (awaiting approval)
accounts; type-ahead lists only active ones; disabled accounts never appear.
Lookup text is never logged at INFO or above (it is personal data)."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MODES = ("exact", "domain", "open")
TYPE_AHEAD_MIN = 2
MAX_RESULTS = 10


@dataclass(frozen=True)
class DirectoryConfig:
    mode: str
    domains: frozenset[str]
    show_email: bool


def load_directory_config(env: Mapping[str, str] = os.environ) -> DirectoryConfig:
    """Read at request time. An invalid value logs a WARNING and falls back
    to the safe default (exact; emails hidden)."""
    mode = env.get("USER_DIRECTORY_MODE", "").strip().lower() or "exact"
    if mode not in MODES:
        logger.warning("USER_DIRECTORY_MODE=%r is not exact, domain or open; using exact", mode)
        mode = "exact"
    domains = frozenset(d.strip().lower() for d in env.get("USER_DIRECTORY_DOMAINS", "").split(",")
                        if d.strip())
    raw = env.get("USER_DIRECTORY_SHOW_EMAIL", "").strip().lower() or "false"
    if raw not in ("true", "false"):
        logger.warning("USER_DIRECTORY_SHOW_EMAIL=%r is not true or false; using false", raw)
        raw = "false"
    return DirectoryConfig(mode=mode, domains=domains, show_email=raw == "true")


def mask_email(email: str | None) -> str:
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}•••@{domain}" if domain else "•••"


def shown_username(username: str | None, show_email: bool) -> str | None:
    """A username, unless it is email-shaped while emails are hidden."""
    if not username or ("@" in username and not show_email):
        return None
    return username


def person_label(*, first_name, last_name, display_name, username, email, show_email: bool) -> str:
    """The one labelling rule (A0 §4): "First Last", else the display name,
    else @username, else the email (shown) or a masked email (hidden)."""
    full = " ".join(p for p in (first_name, last_name) if p)
    if full:
        return full
    if display_name:
        return display_name
    uname = shown_username(username, show_email)
    if uname:
        return f"@{uname}"
    return email if (show_email and email) else mask_email(email)


def person_cols(alias: str) -> str:
    """The six columns `person_from` reads, in order."""
    return (f"{alias}.id, {alias}.first_name, {alias}.last_name, {alias}.display_name, "
            f"{alias}.username, {alias}.email")


def person_from(cols: Sequence, show_email: bool) -> dict | None:
    """`{"id", "name"}` from `person_cols` values; None for a deleted person."""
    if cols[0] is None:
        return None
    return {"id": str(cols[0]),
            "name": person_label(first_name=cols[1], last_name=cols[2], display_name=cols[3],
                                 username=cols[4], email=cols[5], show_email=show_email)}


def _like_prefix(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _result(r, show_email: bool) -> dict:
    out = {**person_from(r[:6], show_email), "username": shown_username(r[4], show_email),
           "status": r[6]}
    if show_email:
        out["email"] = r[5]
    return out


async def lookup(conn, cfg: DirectoryConfig, caller_id: str, caller_email: str,
                 q: str) -> list[dict]:
    q = q.strip()
    if len(q) < TYPE_AHEAD_MIN:
        return []
    cols = f"{person_cols('u')}, u.status"
    if "@" in q:
        cur = await conn.execute(
            f"SELECT {cols} FROM users u WHERE lower(u.email) = lower(%s) "
            "AND u.status IN ('active', 'pending') AND u.id <> %s", (q, caller_id))
        exact = [_result(r, cfg.show_email) for r in await cur.fetchall()]
        if exact:
            logger.debug("directory lookup: exact match, %d result(s)", len(exact))
            return exact
    mode = cfg.mode
    domain = (caller_email or "").rpartition("@")[2].lower()
    if mode == "domain" and domain not in cfg.domains:
        mode = "exact"
    if mode == "exact":
        logger.debug("directory lookup: mode=exact, no type-ahead")
        return []
    pattern = _like_prefix(q.lower())
    matches = ["lower(u.first_name) LIKE %s", "lower(u.last_name) LIKE %s",
               "lower(u.display_name) LIKE %s",
               "(lower(u.username) LIKE %s AND (%s OR position('@' in u.username) = 0))"]
    params: list = [pattern, pattern, pattern, pattern, cfg.show_email]
    if cfg.show_email:
        matches.append("lower(u.email) LIKE %s")
        params.append(pattern)
    where = f"u.status = 'active' AND u.id <> %s AND ({' OR '.join(matches)})"
    params = [caller_id, *params]
    if mode == "domain":
        where += " AND lower(split_part(u.email, '@', 2)) = %s"
        params.append(domain)
    cur = await conn.execute(
        f"SELECT {cols} FROM users u WHERE {where} "
        "ORDER BY lower(coalesce(u.first_name, u.display_name, u.username, u.email)), u.id "
        f"LIMIT {MAX_RESULTS}", params)
    found = [_result(r, cfg.show_email) for r in await cur.fetchall()]
    logger.debug("directory lookup: mode=%s, %d result(s)", mode, len(found))
    return found
