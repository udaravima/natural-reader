"""A line's recorded lines: their default texts, rendering them, and the in-memory cache a call reads (spec 5.1).

Clips are rendered when a line is saved, so a caller never waits on a voice."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.phone import store, voice
from server.phone.store import Clip, Line

logger = logging.getLogger(__name__)

# `greeting` comes from the line itself. The two `filler` texts are played when a tool starts and nothing has been
# said yet; `sorry` serves both a misheard clip and a failed answer.
DEFAULT_CLIP_TEXTS: dict[str, list[str]] = {
    "busy": ["Sorry, this line is busy. Please call again later."],
    "closed": ["Sorry, this line isn't taking calls right now. Goodbye."],
    "goodbye": ["Thank you for calling. Goodbye."],
    "still": ["Are you still there?"],
    "sorry": ["Sorry, I didn't get that. Could you say it again?"],
    "filler": ["Let me check.", "One moment."],
}


async def render_clips(line: Line) -> list[Clip]:
    """Every clip of the line in its voice. All share one `rendered_at`; saving them replaces the set, which moves
    the cache's stamp. A voice failure raises as the engine raised it."""
    texts = {"greeting": [line.greeting], **DEFAULT_CLIP_TEXTS}
    key = voice.voice_key(line.voice)
    now = datetime.now(timezone.utc)
    return [Clip(kind, ordinal, text, key, await voice.render(text, line.voice), now)
            for kind, lines in texts.items() for ordinal, text in enumerate(lines)]


class ClipCache:
    """The clips per line, kept in memory. `get` asks the database only for the newest `rendered_at` and reloads
    the clips when that or the line's voice changed; `peek` never touches the database."""

    def __init__(self) -> None:
        self._held: dict[str, tuple[datetime | None, str, dict[str, list[bytes]]]] = {}

    async def get(self, conn, line: Line) -> dict[str, list[bytes]]:
        """kind -> pcm in ordinal order (`filler` has two)."""
        stamp = await store.clips_stamp(conn, line.id)
        key = voice.voice_key(line.voice)
        held = self._held.get(line.id)
        if held and held[0] == stamp and held[1] == key:
            return held[2]
        clips = await store.load_clips(conn, line.id)
        if any(c.voice_key != key for c in clips):
            # A stale voice beats silence: the caller still hears the line, and a re-save will fix it.
            logger.warning("phone line %s: its clips were made in another voice than its current one; using them",
                           line.id)
        by_kind: dict[str, list[bytes]] = {}
        for c in sorted(clips, key=lambda c: (c.kind, c.ordinal)):
            by_kind.setdefault(c.kind, []).append(c.pcm)
        self._held[line.id] = (stamp, key, by_kind)
        return by_kind

    def peek(self, line_id: str) -> dict[str, list[bytes]] | None:
        """What is held for the line, or None. For when the database is down."""
        held = self._held.get(line_id)
        return held[2] if held else None
