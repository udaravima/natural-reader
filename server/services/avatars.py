"""Profile pictures: one small PNG, JPEG or WebP per person.

The browser crops and resizes the picture before upload (to 256 px, a few
tens of KB). The server never decodes it: it checks the leading bytes are one
of the three raster formats and the size is at most MAX_BYTES, then serves it
back with its own content type, nosniff and a no-script CSP, so an upload
can't become a page that runs in the app's origin (an SVG, an HTML file
renamed .png)."""
from __future__ import annotations

from datetime import datetime

MAX_BYTES = 256 * 1024


def sniff(data: bytes) -> str | None:
    """The image type the bytes start with, or None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def url(user_id: str, updated_at: datetime | None) -> str | None:
    """Where the picture is served; the version changes with every upload, so
    a browser that cached the old one fetches the new one."""
    if updated_at is None:
        return None
    return f"/v1/users/{user_id}/avatar?v={int(updated_at.timestamp())}"


async def save(conn, user_id: str, data: bytes, content_type: str) -> datetime:
    cur = await conn.execute(
        "INSERT INTO user_avatars (user_id, content_type, data) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET content_type = EXCLUDED.content_type, "
        "data = EXCLUDED.data, updated_at = now() RETURNING updated_at",
        (user_id, content_type, data))
    return (await cur.fetchone())[0]


async def remove(conn, user_id: str) -> None:
    await conn.execute("DELETE FROM user_avatars WHERE user_id = %s", (user_id,))


async def load(conn, user_id: str) -> tuple[str, bytes, datetime] | None:
    cur = await conn.execute(
        "SELECT content_type, data, updated_at FROM user_avatars WHERE user_id = %s", (user_id,))
    row = await cur.fetchone()
    return (row[0], bytes(row[1]), row[2]) if row else None


async def updated_at(conn, user_id: str) -> datetime | None:
    cur = await conn.execute("SELECT updated_at FROM user_avatars WHERE user_id = %s", (user_id,))
    row = await cur.fetchone()
    return row[0] if row else None
