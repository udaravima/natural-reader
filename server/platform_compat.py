"""Windows bare-metal fixes, applied once when the `server` package loads.

- A Windows console defaults to cp1252, which can't encode the ✓ / → the
  backend prints and logs: the first such print raises UnicodeEncodeError.
- psycopg's async mode can't run on asyncio's ProactorEventLoop, the Windows
  default. The selector policy covers asyncio.run (tests, scripts); uvicorn
  picks its own Windows loop regardless, so run.py passes uvicorn_loop().
"""
from __future__ import annotations

import asyncio
import sys


def apply(platform: str | None = None, stdout=None, stderr=None) -> None:
    platform = platform or sys.platform
    if platform != "win32":
        return
    for stream in (stdout or sys.stdout, stderr or sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy is not None:
        asyncio.set_event_loop_policy(policy())


def uvicorn_loop(platform: str | None = None) -> str:
    """uvicorn's `loop` setting: a selector loop on Windows, else its default."""
    return "asyncio:SelectorEventLoop" if (platform or sys.platform) == "win32" else "auto"
