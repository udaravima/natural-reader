"""Windows bare-metal fixes, exercised from any OS: a cp1252 console can't
print the ✓ / → the backend logs, and uvicorn's default Windows loop
(ProactorEventLoop) is one psycopg's async mode refuses."""
import asyncio

import uvicorn

from server import platform_compat


class _Console:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kw):
        self.calls.append(kw)


def test_a_windows_console_is_switched_to_utf8():
    out, err = _Console(), _Console()
    platform_compat.apply("win32", out, err)
    assert out.calls == err.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_other_platforms_are_left_alone():
    out = _Console()
    platform_compat.apply("linux", out, out)
    assert out.calls == []


def test_missing_or_plain_streams_are_skipped():
    # pythonw has no console (None); a redirected stream may lack reconfigure.
    platform_compat.apply("win32", None, object())


def test_windows_uvicorn_runs_a_selector_loop():
    factory = uvicorn.Config(app=lambda *a: None, loop=platform_compat.uvicorn_loop("win32")).get_loop_factory()
    loop = factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_elsewhere_uvicorn_keeps_its_default_loop():
    assert platform_compat.uvicorn_loop("linux") == "auto"
