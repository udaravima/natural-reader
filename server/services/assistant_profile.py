"""What a profile's text is made of (v2.4 Task A2): how it is cleaned and capped, the warning for a tool it names,
and the file a deployer can keep one in.

The profiles themselves live in the profile library (services/profile_library.py): the admin's house rules and
named profiles, one chosen per chat. The file at CHAT_ASSISTANT_PROFILE_FILE is the profile only while that
library is empty (chat/profile_text.py); it is read on every turn, since it is small, and a deployer's edit then
applies without a restart.

Profile text is the deployer's trusted instruction, so it isn't fenced; only admins can change it. It is never
logged, at any level.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

PROFILE_MAX_CHARS = 8000   # characters; every one is read before every reply
# The app describes the tools offered each turn; a profile that names one
# that isn't offered sends the model calling it in vain.
APP_TOOLS = ("search_documents", "read_document_pages", "web_search", "current_time")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")   # all but \t and \n
_warned_paths: set[str] = set()


def clean_profile(text: str) -> str:
    """Control characters except newline and tab removed, trailing
    whitespace stripped, capped at PROFILE_MAX_CHARS."""
    return _CONTROL.sub("", text).rstrip()[:PROFILE_MAX_CHARS]


def profile_warnings(text: str) -> list[str]:
    named = [t for t in APP_TOOLS if t in text]
    if not named:
        return []
    return [f"It names {', '.join(named)}. The app describes the tools available on each turn; naming one "
            "that isn't offered makes the model try to call it. Describe the behaviour instead."]


def file_path() -> str | None:
    return (os.environ.get("CHAT_ASSISTANT_PROFILE_FILE") or "").strip() or None


def _from_file() -> str:
    path = file_path()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(PROFILE_MAX_CHARS * 4)
    except OSError as e:
        if path not in _warned_paths:   # once per path, not once per turn
            _warned_paths.add(path)
            logger.warning("CHAT_ASSISTANT_PROFILE_FILE %s can't be read (%s): no profile from it",
                           path, type(e).__name__)
        return ""
    _warned_paths.discard(path)
    return clean_profile(text)
