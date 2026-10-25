"""The admin's limits on what a Docling conversion may use (admin console v2 spec §4.3), and the rules a convert
request is held to.

Conversion is the server's heaviest job, so an admin can narrow it: who may start one, which presets, whether OCR
and picture descriptions are on, and how many pages one conversion may cover. The five settings resolve like every
console setting (services/console_settings.py): the console's value, then the DOCLING_* environment variable, then
the default, which allows everything. The console can only narrow what the environment allows: the preset lists are
intersected, the switches are ANDed, the page limit is the lower of the two, and "role" wins over "readers".

`convert_by` is "readers" (anyone who can read, today's behaviour) or "role" (people holding the `docling`
capability, a Keycloak realm role, plus admins, who may always convert).

`check` is the one place the rules live; POST /v1/docs/{id}/convert calls it before it marks anything as converting,
and the settings route hands the browser `user_view` so the convert dialog can grey out what would be refused.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from . import console_settings
from .console_settings import Section, Setting

PRESETS = ("fast", "standard", "accurate")
CONVERT_BY = ("readers", "role")
DOCLING_CAPABILITY = "docling"
MAX_PAGES_LIMIT = 100000

_BOOL_ERROR = "Must be true or false."
_PAGES_ERROR = f"Must be a whole number from 0 to {MAX_PAGES_LIMIT}."
_CONVERT_BY_ERROR = "Must be readers or role."


# ---------- parsing: what the console may store, what the environment may say ----------

def _convert_by(value: Any, _fallback: Any = None) -> str:
    if isinstance(value, str) and value.strip().lower() in CONVERT_BY:
        return value.strip().lower()
    raise ValueError(_CONVERT_BY_ERROR)


def _narrow_convert_by(console: str, env: str) -> str:
    return "role" if "role" in (console, env) else "readers"


def _preset_names(names: Any) -> list[str]:
    """Known preset names, de-duplicated and in PRESETS order (so a stored list always reads the same)."""
    if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
        raise ValueError("Choose from fast, standard and accurate.")
    wanted = {n.strip().lower() for n in names}
    unknown = wanted - set(PRESETS)
    if unknown:
        raise ValueError(f"Unknown preset: {', '.join(sorted(unknown))}. Choose from fast, standard and accurate.")
    return [p for p in PRESETS if p in wanted]


def _presets(value: Any, fallback: Any) -> list[str]:
    chosen = _preset_names(value)
    if not chosen:
        raise ValueError("Keep at least one preset.")
    if not [p for p in chosen if p in fallback]:
        raise ValueError("None of these presets is allowed by the server setting DOCLING_PRESETS.")
    return chosen   # a list, not a tuple: the audit line compares it with the JSON the row stores


def _presets_env(text: str) -> list[str]:
    chosen = _preset_names([part for part in text.split(",") if part.strip()])
    if not chosen:
        raise ValueError("no presets")
    return chosen


def _narrow_presets(console: list[str], env: list[str]) -> list[str]:
    return [p for p in PRESETS if p in console and p in env]


def _switch(value: Any, _fallback: Any = None) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(_BOOL_ERROR)


def _switch_env(text: str) -> bool:
    word = text.strip().lower()
    if word in ("true", "1", "yes"):
        return True
    if word in ("false", "0", "no"):
        return False
    raise ValueError(_BOOL_ERROR)


def _max_pages(value: Any, _fallback: Any = None) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_PAGES_LIMIT:
        return value
    raise ValueError(_PAGES_ERROR)


def _max_pages_env(text: str) -> int:
    try:
        return _max_pages(int(text.strip()))
    except ValueError:
        raise ValueError(_PAGES_ERROR) from None


def _narrow_max_pages(console: int, env: int) -> int:
    """0 means no limit, so the lower limit is the lower of the nonzero ones."""
    return min((n for n in (console, env) if n > 0), default=0)


SECTION = Section("documents", (
    Setting("convert_by", env="DOCLING_CONVERT_BY", default="readers", parse=_convert_by, parse_env=_convert_by,
            narrow=_narrow_convert_by),
    Setting("presets", env="DOCLING_PRESETS", default=list(PRESETS), parse=_presets, parse_env=_presets_env,
            narrow=_narrow_presets),
    Setting("ocr", env="DOCLING_ALLOW_OCR", default=True, parse=_switch, parse_env=_switch_env,
            narrow=lambda console, env: console and env),
    Setting("picture_description", env="DOCLING_ALLOW_PICTURE_DESCRIPTION", default=True, parse=_switch,
            parse_env=_switch_env, narrow=lambda console, env: console and env),
    Setting("max_pages", env="DOCLING_MAX_PAGES", default=0, parse=_max_pages, parse_env=_max_pages_env,
            narrow=_narrow_max_pages),
))


# ---------- the limits a request meets ----------

@dataclass(frozen=True)
class ConvertLimits:
    convert_by: str
    presets: tuple[str, ...]
    ocr: bool
    picture_description: bool
    max_pages: int      # 0 = no limit


async def load_limits(conn, env: Mapping[str, str] = os.environ) -> ConvertLimits:
    v = {name: value.value for name, value in (await console_settings.load(conn, SECTION, env)).items()}
    return ConvertLimits(v["convert_by"], tuple(v["presets"]), v["ocr"], v["picture_description"], v["max_pages"])


@dataclass(frozen=True)
class Refused:
    option: str     # the setting that said no: convert_by | preset | ocr | picture_description | max_pages
    message: str    # written for the person who asked


def may_convert(limits: ConvertLimits, *, is_admin: bool, capabilities: frozenset[str]) -> bool:
    return limits.convert_by == "readers" or is_admin or DOCLING_CAPABILITY in capabilities


def pages_requested(page_range: list[int] | None, total: int | None) -> int | None:
    """How many pages a conversion would cover: the range (cut at the document's end when that is known), else
    the whole document. None when neither is known."""
    if page_range:
        start, end = page_range
        return min(end, total or end) - start + 1
    return total


def check(limits: ConvertLimits, options: dict, *, is_admin: bool, capabilities: frozenset[str],
          pages: int | None) -> Refused | None:
    """The first limit this request breaks, or None. `options` is ConvertOptionsIn.model_dump() (a missing key
    is the model's default); `pages` is pages_requested(), None when unknown (Docling's own failure reports it)."""
    if not may_convert(limits, is_admin=is_admin, capabilities=capabilities):
        return Refused("convert_by", "Only people your admin has given the Docling role can convert documents "
                                     "on this server.")
    preset = options.get("preset", "standard")
    if preset not in limits.presets:
        return Refused("preset", f"Your admin has turned off the {preset.capitalize()} preset.")
    if options.get("ocr") and not limits.ocr:
        return Refused("ocr", "Your admin has turned off OCR for conversions.")
    if options.get("images") == "describe" and not limits.picture_description:
        return Refused("picture_description", "Your admin has turned off picture descriptions for conversions.")
    if pages is not None and pages > limits.max_pages > 0:
        n = limits.max_pages
        return Refused("max_pages", f"This server converts at most {n} pages at a time. "
                                    f"Choose a page range of {n} pages or fewer.")
    return None


def user_view(limits: ConvertLimits, *, is_admin: bool, capabilities: frozenset[str], enabled: bool) -> dict:
    """What the convert dialog needs from GET /v1/settings/docling: whether to offer conversion at all, and
    which options are off. `enabled` is the server's DOCLING_ENABLED."""
    return {"enabled": enabled, "may_convert": may_convert(limits, is_admin=is_admin, capabilities=capabilities),
            "presets": list(limits.presets), "ocr": limits.ocr, "picture_description": limits.picture_description,
            "max_pages": limits.max_pages}


# ---------- the server's own values, read-only beside the settings ----------

def _row(id: str, label: str, value: str, var: str, env: Mapping[str, str]) -> dict:
    return {"id": id, "label": label, "value": value, "env": var,
            "source": "env" if (env.get(var) or "").strip() else "default"}


def info_rows(env: Mapping[str, str] = os.environ) -> list[dict]:
    docling_on = (env.get("DOCLING_ENABLED") or "false").strip().lower() in ("1", "true", "yes")
    return [
        _row("docling", "Docling", "On" if docling_on else "Off", "DOCLING_ENABLED", env),
        _row("pdf_upload_max", "Largest PDF upload", f"{(env.get('PDF_UPLOAD_MAX_MB') or '').strip() or '50'} MB",
             "PDF_UPLOAD_MAX_MB", env),
        _row("text_upload_max", "Largest text upload", f"{(env.get('TEXT_UPLOAD_MAX_MB') or '').strip() or '10'} MB",
             "TEXT_UPLOAD_MAX_MB", env),
    ]
