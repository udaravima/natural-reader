"""What the deployment calls the app: its name, its tagline and an optional copyright line.

Three environment values, read once at start (a change needs a restart):
- APP_NAME: unset = "Natural Reader". At most 40 characters. A first-deployment choice: the identity
  provider's realm carries its own display name, and the two should match (docs/DEPLOYMENT.md).
- APP_TAGLINE: what the app is, after "<name>, " in the chat's first line and under the name on the
  sign-in screen. Unset = "an app for reading and listening to documents". At most 120 characters.
- APP_COPYRIGHT: shown as written at the foot of the sign-in screen and of Settings. Unset = no line.
  At most 80 characters.

Each is made one clean line: control characters go, runs of whitespace become one space.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

NAME_MAX_CHARS = 40
TAGLINE_MAX_CHARS = 120
COPYRIGHT_MAX_CHARS = 80
DEFAULT_NAME = "Natural Reader"
DEFAULT_TAGLINE = "an app for reading and listening to documents"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Identity:
    name: str
    tagline: str
    copyright: str


def _line(value: str | None, limit: int) -> str:
    return " ".join(_CONTROL.sub(" ", value or "").split())[:limit].rstrip()


def load(env: Mapping[str, str] | None = None) -> Identity:
    env = os.environ if env is None else env
    return Identity(name=_line(env.get("APP_NAME"), NAME_MAX_CHARS) or DEFAULT_NAME,
                    tagline=_line(env.get("APP_TAGLINE"), TAGLINE_MAX_CHARS) or DEFAULT_TAGLINE,
                    copyright=_line(env.get("APP_COPYRIGHT"), COPYRIGHT_MAX_CHARS))


@lru_cache(maxsize=1)
def current() -> Identity:
    return load()
