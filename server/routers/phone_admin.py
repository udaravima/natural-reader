"""/v1/admin/phone/* — phone lines for the admin (spec §5.7, §5.10): the listener's state, the voices a line may
speak with, the lines (their profile, project, model, voice, welcome and limits), the welcome as a WAV, and each
line's calls with their transcripts.

Every route needs the `admin` capability. Putting a project on a line also needs the admin to be a maintainer or
owner of that project (R2.6), because the line publishes it to anyone who dials. Reading a line's project name or its
transcripts needs no project role (R11.10): a project on a line is already public to its callers.

A save never holds a pool connection across a voice render or a provider request (R11.5, R11.7). It reads what it
needs on one short connection, checks the model and renders the clips with none, then writes the line, its
membership and its clips in one transaction on another. A render failure writes nothing.

Logs carry ids only, never a welcome message or a transcript."""
from __future__ import annotations

import contextlib
import dataclasses
import io
import ipaddress
import logging
import os
import re
import wave
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Annotated, Any, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from .. import chat_voice, tts_language
from ..auth import deps
from ..auth.authz import require_project_role
from ..db import get_pool
from ..http_errors import refusal
from ..llm.router import get_router
from ..phone import clips, service as phone_service, store, voice
from ..phone.config import split_listen
from ..phone.store import Clip, Line, LineVoice
from ..services import inference_budget, model_router, model_settings, profile_library
from ..services.deployment_settings import deployment_zone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin/phone", tags=["admin-phone"])

SNIPPET_EXTEN = "810"     # the snippet's example extension: the console tells the admin to pick any free one
LIVE_GRACE_S = 60         # an open call older than its line's longest call plus this was left by a crash (R11.2)
CALLS_MAX = 200           # calls listed at once
# Kokoro reads a voice's text in the language its id's first letter names (tts_language), and a phase-1 line speaks
# English (R11.11): only these Kokoro voices may be a line's. Phase 2's language menu widens this per line language.
LINE_KOKORO_LANGUAGES = frozenset({"en-us", "en-gb"})
# What the voices themselves raise when they can't speak (R11.12). Google's and Chirp's every failure is a
# chat_voice.GoogleVoiceError. Kokoro, through endpoints.kokoro_pcm, raises kokoro_onnx's own ValueError (a voice,
# phonemes or length it refuses) and RuntimeError (espeak), and voice.render a ValueError for an unknown engine.
# Their messages can quote the text, so they are logged by type alone; anything else is a bug in the render and is
# logged with its traceback.
_VOICE_ERRORS = (chat_voice.GoogleVoiceError, ValueError, RuntimeError)

_NOT_FOUND = "This phone line doesn't exist."
_CALL_NOT_FOUND = "This call doesn't exist."
# The chat's own words for each model_refusal code (routers/chat_turns.post_turn), so a line's save reads as a turn.
_MODEL_WORDS = {"not_listed": "This model isn't available.",
                "not_allowed": "That model isn't available on this server."}


def phone_conn() -> Callable[[], AbstractAsyncContextManager]:
    """The seam for the short connections these routes take: a callable that opens a pooled connection, which
    commits when its block ends. A dependency, so a test can hand over its own. Not `deps.get_conn`: that one is
    held for the whole route, and a save waits on the voice and the model provider."""
    return lambda: get_pool().connection()


def _refuse(status: int, reason: str, message: str):
    """The house refusal shape, with the code also as `reason` (the console's switch, as for R1.3's 409s)."""
    return refusal(status, reason, message, reason=reason)


@contextlib.contextmanager
def _store_errors():
    """R11.3: the store's LookupError is a 404 and its ValueError a 422. put_member's and require_project_role's
    HTTPExceptions pass through as they are."""
    try:
        yield
    except LookupError:
        raise _refuse(404, "not_found", _NOT_FOUND) from None
    except ValueError as e:
        raise _refuse(422, "invalid_line", str(e)) from None


# ---------- what a body may say: migration 033's CHECKs, answered before the database would raise (R11.8) ----------

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Greeting = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^\S+$")]
MaxCallS = Annotated[int, Field(ge=60, le=3600)]         # seconds
MaxCalls = Annotated[int, Field(ge=1, le=20)]
DailyTokens = Annotated[int, Field(ge=0, le=2**63 - 1)]  # users.inference_daily_token_budget is a BIGINT


class VoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["kokoro", "google", "chirp"]
    voice: str = Field(min_length=1, max_length=60)
    accent: str | None = Field(default=None, max_length=10)   # Google's English accent; Kokoro has none


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    enabled: StrictBool = False
    profileId: UUID | None = None      # null: the default profile
    projectId: UUID | None = None      # null: the line can't be switched on
    model: ModelId
    voice: VoiceIn
    language: Literal["en"] = "en"     # phase 1
    greeting: Greeting
    maxCallS: MaxCallS = 600
    maxCalls: MaxCalls = 2
    dailyTokens: DailyTokens | None = None   # null: the deployment's default


class LinePatch(BaseModel):
    """Only the fields the body holds change (R11.3). profileId, projectId and dailyTokens may be null, as on
    create; the rest may be left out but not nulled."""
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    enabled: StrictBool | None = None
    profileId: UUID | None = None
    projectId: UUID | None = None
    model: ModelId | None = None
    voice: VoiceIn | None = None
    language: Literal["en"] | None = None
    greeting: Greeting | None = None
    maxCallS: MaxCallS | None = None
    maxCalls: MaxCalls | None = None
    dailyTokens: DailyTokens | None = None

    @model_validator(mode="after")
    def _required_stay_set(self):
        for field in ("name", "enabled", "model", "voice", "language", "greeting", "maxCallS", "maxCalls"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} can't be null")
        return self


_STORE_FIELDS = {"name": "name", "enabled": "enabled", "profileId": "profile_id", "projectId": "project_id",
                 "model": "model_id", "voice": "voice", "greeting": "greeting", "maxCallS": "max_call_s",
                 "maxCalls": "max_calls", "dailyTokens": "daily_tokens"}


def _line_voice(v: VoiceIn) -> LineVoice:
    """Kokoro takes no accent: one sent along (a picker switching engines) is dropped, so it can't change the
    clips' voice key."""
    return LineVoice(v.engine, v.voice, v.accent if v.engine in chat_voice.GOOGLE_ENGINES else None)


def _changes(body: LinePatch) -> dict[str, Any]:
    """The body as update_line's keywords: only what it holds. `language` is fixed in phase 1, so not passed."""
    out: dict[str, Any] = {}
    for key in body.model_fields_set - {"language"}:
        value = getattr(body, key)
        if key in ("profileId", "projectId") and value is not None:
            value = str(value)         # canonical, so the store's same-project comparison holds
        elif key == "voice":
            value = _line_voice(value)
        out[_STORE_FIELDS[key]] = value
    return out


# ---------- voices ----------

def _kokoro_voices() -> list[str]:
    from ..model import kokoro   # imported here: importing it loads Kokoro
    return kokoro.get_voices()


def _voices_of(engine: str) -> list[str]:
    """The voices a line may speak with on `engine`: what /voices offers and what a save accepts (R11.11).
    Kokoro's English ones only; Google's and Chirp's all, since a line sets their English accent itself."""
    if engine == "kokoro":
        return [v for v in _kokoro_voices() if tts_language.language_of(v) in LINE_KOKORO_LANGUAGES]
    return list(chat_voice.GOOGLE_VOICES)


def _check_voice(v: LineVoice) -> None:
    """The engine must be one this server offers (chat_voice.engines), the voice one of its own, and a Google
    accent one Google's voices take."""
    needs = chat_voice.needs(v.engine)
    if needs:
        raise _refuse(422, "engine_not_configured",
                      f"{chat_voice.LABELS[v.engine]} isn't set up on this server: it needs {needs}.")
    if v.voice not in _voices_of(v.engine):
        raise _refuse(422, "unknown_voice", f"That isn't one of {chat_voice.LABELS[v.engine]}'s voices.")
    if v.accent is not None and v.accent not in chat_voice.ACCENT_CODES:
        raise _refuse(422, "unknown_accent", "That isn't one of the English accents Google's voices offer.")


@router.get("/voices")
async def voices(_: deps.Principal = Depends(deps.require_admin)):
    """The engines this server offers a line, each with its voices, and Google's with their English accents."""
    out = []
    for engine in chat_voice.engines():
        if not engine["available"]:
            continue
        entry = {"engine": engine["id"], "label": engine["label"], "voices": _voices_of(engine["id"])}
        if engine["id"] in chat_voice.GOOGLE_ENGINES:
            entry["accents"] = chat_voice.accents()
        out.append(entry)
    return {"engines": out}


# ---------- the listener ----------

@router.get("/status")
async def status(_: deps.Principal = Depends(deps.require_admin)):
    """Listener on or off, the address the PBX dials, the allow list, and this process's calls now. The address is
    the one the snippet would carry (`_dialable`): one nobody can dial reads as none, so the console's header says
    to set PHONE_PBX_ADDRESS instead of naming a wildcard (R12.1)."""
    listener = phone_service.status()
    return {**listener, "pbx_address": _dialable(listener.get("pbx_address"))}


_BRACKETED = re.compile(r"^\[[^\]]+\]:\d+$")


def _dialable(address: str | None) -> str | None:
    """R11.1a: PHONE_PBX_ADDRESS isn't checked at load, so an address with no port (or port 0), or with an
    unspecified host (0.0.0.0, ::, or the `0` shorthand), is no address: nobody can dial it. Nor is an IPv6 host
    without its brackets (R11.13): in `2001:db8::1` no port can be told apart, and split_listen would read `:1` as
    one. A bracketed host must be exactly `[host]:port` (final fix F15): split_listen strips one bracket from each
    end, so `[2001:db8::1:9092` would otherwise read as host 2001:db8::1, port 9092."""
    if address and address.strip().startswith("[") and not _BRACKETED.match(address.strip()):
        return None
    parts = split_listen(address) if address else None
    if parts is None or parts[1] == 0:
        return None
    host = parts[0]
    if host == "0" or (":" in host and not address.strip().startswith("[")):
        return None
    try:
        if ipaddress.ip_address(host).is_unspecified:
            return None
    except ValueError:
        pass                           # a name
    return address.strip()


def _snippet(line_id: str, listener: dict) -> str | None:
    """The three dialplan lines that send an extension's calls to this line (spec §5.8). None while the listener is
    off, or when there is no address to dial (R11.1): never a snippet with a placeholder or a wildcard in it."""
    address = _dialable(listener.get("pbx_address")) if listener.get("listening") else None
    if address is None:
        return None
    return (f"exten => {SNIPPET_EXTEN},1,Answer()\n"
            f" same => n,AudioSocket({line_id},{address})\n"
            f" same => n,Hangup()")


# ---------- a line as the console sees it ----------

async def _closed_reason(conn, ln: Line, has_access: bool, models: model_settings.ModelsSettings, router,
                         deployment_budget: int | None) -> str | None:
    """Why the gate turns this line's callers away, or None when it answers (final fix F19): the gate's own offline
    checks in its own order (service.py steps 2 to 4), so the list and a call agree. `off`, `no_project`,
    `no_access`, `budget` or `model`. The provider's own model list is never asked here (it needs the network): a
    model the provider dropped shows only at the next call. `router` None: it hasn't started, so the model isn't
    judged."""
    if not ln.enabled:
        return "off"
    if ln.project_id is None:
        return "no_project"
    if not has_access:
        return "no_access"
    if await inference_budget.over_budget(conn, ln.service_user_id, deployment_budget):
        return "budget"
    if router is not None and not model_settings.model_offered(models, ln.model_id, router):
        return "model"
    return None


async def _views(conn, lines: list[Line]) -> list[dict]:
    """The line JSON for each line, from six reads whatever their number, plus the budget reads of the lines that are
    on and can reach their project."""
    if not lines:
        return []
    ids = [ln.id for ln in lines]
    profile_ids = [ln.profile_id for ln in lines if ln.profile_id]
    project_ids = [ln.project_id for ln in lines if ln.project_id]
    # A profile switched off in the library stays the line's, but a call answers as the default profile
    # (profile_text.resolve skips a disabled one): `enabled` lets the console say so.
    cur = await conn.execute("SELECT id::text, name, enabled FROM assistant_profiles WHERE id = ANY(%s::uuid[])",
                             (profile_ids,))
    profiles = {pid: {"id": pid, "name": name, "enabled": enabled} for pid, name, enabled in await cur.fetchall()}
    cur = await conn.execute("SELECT id::text, name FROM projects WHERE id = ANY(%s::uuid[])", (project_ids,))
    projects = dict(await cur.fetchall())
    # "today" is the deployment's, as a call's title is; a refused call counts, since someone called
    zone = await deployment_zone(conn, os.environ)
    midnight = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    cur = await conn.execute(
        "SELECT line_id::text, count(*) FILTER (WHERE started_at >= %s), max(started_at), count(session_id) "
        "FROM phone_calls WHERE line_id = ANY(%s::uuid[]) GROUP BY line_id", (midnight, ids))
    calls = {lid: (today, last, transcripts) for lid, today, last, transcripts in await cur.fetchall()}
    # the gate's own question: is the line's user still a member of its project (service.py step 2)
    cur = await conn.execute(
        "SELECT l.id::text FROM phone_lines l JOIN project_members m "
        "ON m.project_id = l.project_id AND m.user_id = l.service_user_id WHERE l.id = ANY(%s::uuid[])", (ids,))
    access = {row[0] for row in await cur.fetchall()}
    listener = phone_service.status()
    models = await model_settings.settings_or_env(conn)
    deployment_budget = model_router.get_config().daily_token_budget   # as the gate and post_turn pass it
    try:
        llm = get_router()
    except RuntimeError:
        llm = None
    out = []
    for ln in lines:
        today, last, transcripts = calls.get(ln.id, (0, None, 0))
        out.append({
            "id": ln.id, "name": ln.name, "enabled": ln.enabled,
            "profile": profiles.get(ln.profile_id, {"id": ln.profile_id, "name": None, "enabled": None})
            if ln.profile_id else None,
            "project": {"id": ln.project_id, "name": projects.get(ln.project_id)} if ln.project_id else None,
            "model": ln.model_id,
            "voice": {"engine": ln.voice.engine, "voice": ln.voice.voice, "accent": ln.voice.accent},
            "language": ln.language, "greeting": ln.greeting, "maxCallS": ln.max_call_s, "maxCalls": ln.max_calls,
            "dailyTokens": ln.daily_tokens, "callsToday": today, "lastCallAt": last,
            "transcripts": transcripts,                                    # what deleting the line takes with it
            "access": (ln.id in access) if ln.project_id else None,        # None: no project to have access to
            "closedReason": await _closed_reason(conn, ln, ln.id in access, models, llm, deployment_budget),
            "snippet": _snippet(ln.id, listener),
        })
    return out


async def _view(conn, line: Line) -> dict:
    return (await _views(conn, [line]))[0]


async def _line_or_404(conn, line_id: str) -> Line:
    line = await store.get_line(conn, line_id)
    if line is None:
        raise _refuse(404, "not_found", _NOT_FOUND)
    return line


@router.get("/lines")
async def list_lines(_: deps.Principal = Depends(deps.require_admin), open_conn=Depends(phone_conn)):
    async with open_conn() as conn:
        return await _views(conn, await store.list_lines(conn))


@router.get("/lines/{line_id}")
async def get_line(line_id: UUID, _: deps.Principal = Depends(deps.require_admin), open_conn=Depends(phone_conn)):
    async with open_conn() as conn:
        return await _view(conn, await _line_or_404(conn, str(line_id)))


# ---------- saving ----------

async def _check_model(ms: model_settings.ModelsSettings, model_id: str) -> str:
    """The chat's own rules for whether a model may answer (model_refusal: the environment, the console, the
    provider's list), run with no connection held (R11.5). Returns the canonical id, as a chat stores it."""
    llm = get_router()
    why = await model_settings.model_refusal(ms, model_id, llm)
    if why is not None:
        raise _refuse(422, "model_not_allowed", _MODEL_WORDS.get(why, _MODEL_WORDS["not_allowed"]))
    return llm.canonical_id(model_id)


async def _render(line: Line) -> list[Clip]:
    """Every clip in the line's voice, with no connection held (R11.7). A failure is a 502 with the engine's
    message (Google's is written for people; Kokoro's is the exception's own), whatever was raised (R11.12)."""
    try:
        return await clips.render_clips(line)
    except Exception as exc:  # noqa: BLE001 - any voice failure fails the save, and nothing is written
        logger.warning("phone line %s: its clips couldn't be made (%s)", line.id or "(new)", type(exc).__name__,
                       exc_info=not isinstance(exc, _VOICE_ERRORS))
        raise _refuse(502, "voice_failed", str(exc) or f"The voice failed ({type(exc).__name__}).") from None


async def _check_profile(conn, profile_id: str | None) -> None:
    if profile_id is not None and await profile_library.get_profile(conn, profile_id) is None:
        raise _refuse(422, "unknown_profile", "That assistant profile doesn't exist.")


async def _require_greeting(conn, line_id: str) -> None:
    """Spec §5.7: a line can't be switched on without its welcome recorded."""
    cur = await conn.execute("SELECT 1 FROM phone_line_clips WHERE line_id = %s AND kind = 'greeting'", (line_id,))
    if await cur.fetchone() is None:
        raise _refuse(422, "no_greeting_clip", "This line's welcome hasn't been recorded yet: save it again first.")


def _no_project():
    return _refuse(422, "no_project", "A line needs a project before it can be switched on.")


@router.post("/lines", status_code=201)
async def create_line(body: LineIn, principal: deps.Principal = Depends(deps.require_admin),
                      open_conn=Depends(phone_conn)):
    """A new line with its service user, its project's reader membership and all its clips. Switched on only when
    asked and it has a project."""
    line_voice = _line_voice(body.voice)
    _check_voice(line_voice)
    project_id = str(body.projectId) if body.projectId else None
    profile_id = str(body.profileId) if body.profileId else None
    if body.enabled and project_id is None:
        raise _no_project()
    async with open_conn() as conn:
        if project_id is not None:
            # R2.6, early so a refusal doesn't wait on a render; create_line checks it again in its transaction
            await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
        await _check_profile(conn, profile_id)
        ms = await model_settings.settings_or_env(conn)
    model_id = await _check_model(ms, body.model)
    draft = Line(id="", name=body.name, enabled=False, profile_id=profile_id, project_id=project_id,
                 model_id=model_id, voice=line_voice, language=body.language, greeting=body.greeting,
                 max_call_s=body.maxCallS, max_calls=body.maxCalls, service_user_id="", daily_tokens=body.dailyTokens)
    rendered = await _render(draft)
    async with open_conn() as conn:
        with _store_errors():
            async with conn.transaction():
                line = await store.create_line(
                    conn, principal, name=body.name, profile_id=profile_id, project_id=project_id,
                    model_id=model_id, voice=line_voice, greeting=body.greeting, max_call_s=body.maxCallS,
                    max_calls=body.maxCalls, daily_tokens=body.dailyTokens)
                await store.save_clips(conn, line.id, rendered)
                if body.enabled:
                    await _require_greeting(conn, line.id)
                    line = await store.update_line(conn, principal, line.id, enabled=True)
        return await _view(conn, line)


@router.patch("/lines/{line_id}")
async def update_line(line_id: UUID, body: LinePatch, principal: deps.Principal = Depends(deps.require_admin),
                      open_conn=Depends(phone_conn)):
    """Edit a line. Its clips re-render first when the welcome or the voice changed. The model is checked when the
    body names one, and whenever the line ends up on (R11.6): an off line whose model was withdrawn can still be
    renamed or tidied. A project that stays the same is not a change, so it never re-adds a removed membership."""
    lid = str(line_id)
    changes = _changes(body)
    if "voice" in changes:
        _check_voice(changes["voice"])
    async with open_conn() as conn:
        line = await _line_or_404(conn, lid)
        project_id = changes.get("project_id", line.project_id)
        if changes.get("enabled") and project_id is None:
            raise _no_project()
        if project_id is not None and project_id != line.project_id:
            await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)   # R2.6, early
        await _check_profile(conn, changes.get("profile_id"))
        on_after = bool(changes.get("enabled", line.enabled)) and project_id is not None
        ms = await model_settings.settings_or_env(conn) if "model_id" in changes or on_after else None
    if ms is not None:
        model_id = await _check_model(ms, changes.get("model_id", line.model_id))
        if "model_id" in changes:
            changes["model_id"] = model_id
    after = dataclasses.replace(line, greeting=changes.get("greeting", line.greeting),
                                voice=changes.get("voice", line.voice))
    rendered = None
    if (after.greeting, after.voice) != (line.greeting, line.voice):
        rendered = await _render(after)
    async with open_conn() as conn:
        with _store_errors():
            async with conn.transaction():
                saved = await store.update_line(conn, principal, lid, **changes)
                if rendered is not None:
                    if (saved.greeting, saved.voice) != (after.greeting, after.voice):
                        # another save changed the half this one didn't send while the clips were made
                        raise _refuse(409, "line_changed",
                                      "This line was changed by someone else meanwhile. Load it again and retry.")
                    await store.save_clips(conn, lid, rendered)
                if saved.enabled and changes.get("enabled"):
                    await _require_greeting(conn, lid)
        return await _view(conn, saved)


@router.post("/lines/{line_id}/access")
async def restore_access(line_id: UUID, principal: deps.Principal = Depends(deps.require_admin),
                         open_conn=Depends(phone_conn)):
    """Give the line back its reader membership of its project after a maintainer removed it (R11.4): a
    maintainer's or owner's act, never a side effect of another save."""
    async with open_conn() as conn:
        with _store_errors():
            try:
                line = await store.restore_access(conn, principal, str(line_id))
            except ValueError:
                raise _refuse(422, "no_project", "This line has no project to have access to.") from None
        return await _view(conn, line)


# ---------- deleting ----------

def _in_progress():
    return _refuse(409, "call_in_progress", "A call is in progress on this line. Try again when it has ended.")


_LIVE = ("c.ended_at IS NULL AND c.started_at > now() - (l.max_call_s + %s) * interval '1 second'")


@router.delete("/lines/{line_id}")
async def delete_line(line_id: UUID, principal: deps.Principal = Depends(deps.require_admin),
                      open_conn=Depends(phone_conn)):
    """Delete a line, its calls, clips and service user, with the transcripts. Refused while a call is live
    (R11.2). Returns how many transcripts went."""
    lid = str(line_id)
    async with open_conn() as conn:
        with _store_errors():
            async with conn.transaction():
                # The row lock makes a call that is opening finish first, or wait and then fail its foreign key.
                await conn.execute("SELECT 1 FROM phone_lines WHERE id = %s FOR UPDATE", (lid,))
                cur = await conn.execute(
                    f"SELECT 1 FROM phone_calls c JOIN phone_lines l ON l.id = c.line_id "
                    f"WHERE c.line_id = %s AND {_LIVE} LIMIT 1", (lid, LIVE_GRACE_S))
                if await cur.fetchone() is not None:
                    raise _in_progress()
                transcripts = await store.delete_line(conn, principal, lid)
    return {"transcripts": transcripts}


# ---------- the welcome as a WAV ----------

def _wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.RATE)
        w.writeframes(pcm)
    return out.getvalue()


@router.get("/lines/{line_id}/clips/greeting")
async def greeting_clip(line_id: UUID, _: deps.Principal = Depends(deps.require_admin),
                        open_conn=Depends(phone_conn)):
    """The welcome as the caller hears it (8 kHz mono WAV), for "Hear the welcome". Never cached: a save
    re-records it."""
    async with open_conn() as conn:
        rows = await store.load_clips(conn, str(line_id))
    pcm = next((c.pcm for c in rows if c.kind == "greeting"), None)
    if pcm is None:
        raise _refuse(404, "not_found", "This line has no recorded welcome.")
    return Response(_wav(pcm), media_type="audio/wav", headers={"Cache-Control": "no-store"})


# ---------- calls ----------

def _slowest_first_sound(stats: list) -> float | None:
    """Seconds from the caller stopping to the line's first sound, at the slowest turn (Talk's firstSound)."""
    waits = [s["first_audio_at"] - s["stopped_at"] for s in stats or ()
             if isinstance(s, dict) and s.get("first_audio_at") is not None and s.get("stopped_at") is not None]
    return round(max(waits), 2) if waits else None


def _call_view(call: dict) -> dict:
    ended = call["ended_at"]
    return {"id": call["id"], "startedAt": call["started_at"], "endedAt": ended,
            "lengthS": round((ended - call["started_at"]).total_seconds(), 1) if ended else None,
            "endReason": call["end_reason"], "turns": call["turns"],
            "slowestFirstSoundS": _slowest_first_sound(call["stats"]),
            "transcript": call["session_id"] is not None}


@router.get("/lines/{line_id}/calls")
async def list_calls(line_id: UUID, limit: int = Query(50, ge=1, le=CALLS_MAX),
                     _: deps.Principal = Depends(deps.require_admin), open_conn=Depends(phone_conn)):
    """The line's calls, newest first: when, how long, why it ended, turns, slowest first sound."""
    async with open_conn() as conn:
        await _line_or_404(conn, str(line_id))
        rows = await store.list_calls(conn, str(line_id), limit)
    return [_call_view(c) for c in rows]


@router.get("/calls/{call_id}")
async def get_call(call_id: UUID, _: deps.Principal = Depends(deps.require_admin), open_conn=Depends(phone_conn)):
    """One call with its timings and its transcript (the call's chat), oldest message first."""
    async with open_conn() as conn:
        call = await store.get_call(conn, str(call_id))
    if call is None:
        raise _refuse(404, "not_found", _CALL_NOT_FOUND)
    messages = [{"id": m["id"], "role": m["role"], "content": m["content"], "timestamp": m["timestamp"],
                 "status": m["status"], "finishReason": m["finish_reason"]} for m in call["messages"]]
    return {**_call_view(call), "lineId": call["line_id"], "stats": call["stats"], "messages": messages}


@router.delete("/calls/{call_id}")
async def delete_call(call_id: UUID, _: deps.Principal = Depends(deps.require_admin),
                      open_conn=Depends(phone_conn)):
    """Delete one call and its transcript. Refused while it is live (R11.2). Returns how many transcripts went."""
    cid = str(call_id)
    async with open_conn() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                f"SELECT c.session_id, {_LIVE} FROM phone_calls c JOIN phone_lines l ON l.id = c.line_id "
                f"WHERE c.id = %s FOR UPDATE OF c", (LIVE_GRACE_S, cid))
            row = await cur.fetchone()
            if row is None:
                raise _refuse(404, "not_found", _CALL_NOT_FOUND)
            session_id, live = row
            if live:
                raise _in_progress()
            await store.delete_call(conn, cid)
    return {"transcripts": 1 if session_id else 0}
