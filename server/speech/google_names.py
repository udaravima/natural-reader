"""Google recognizer names (spec 2026-11-04-google-recognizers), with nothing heavy imported: the options and the
routes read them, and must start without the audio packages.

A name is `projects/<p>/locations/<l>/recognizers/<id>`, each part lowercase letters, digits and hyphens, as
Google's own ids are (a project may be its number). The location becomes the host a request goes to, with the
server's Google token, so nothing that can end a host name or start a path, query, fragment or user part may
pass: `evil.example#` would otherwise send the token elsewhere."""
from __future__ import annotations

import re

PART = r"([a-z0-9][a-z0-9-]*)"
NAME = re.compile(rf"^projects/{PART}/locations/{PART}/recognizers/{PART}$")
PART_RE = re.compile(PART)


def is_recognizer_name(value: str | None) -> bool:
    return bool(NAME.match((value or "").strip()))


def split_name(value: str | None) -> tuple[str, str, str] | None:
    """(project, location, id) of a well-formed recognizer name; None for anything else."""
    match = NAME.match((value or "").strip())
    return (match.group(1), match.group(2), match.group(3)) if match else None


def endpoint(location: str) -> str:
    """The Speech v2 endpoint of a location that passed PART_RE: `global` has the plain one."""
    return "https://speech.googleapis.com" if location == "global" else f"https://{location}-speech.googleapis.com"
