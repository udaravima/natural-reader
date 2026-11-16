"""A chat's title from its first exchange (docs/superpowers/specs/2026-11-15-helper-model-design.md §6).

`chat_sessions.title_source` says who wrote a title: 'default', 'cut' (the first message, cut), 'model' or 'person'.
`title_pending_at` says a title is being written; it counts for PENDING_LAPSE at most.
"""
from __future__ import annotations

PENDING_LAPSE = "3 minutes"   # a title still pending after this is given up (a restart mid-call)
PENDING_SQL = f"(s.title_pending_at IS NOT NULL AND s.title_pending_at >= now() - interval '{PENDING_LAPSE}')"
