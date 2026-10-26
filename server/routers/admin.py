"""/v1/admin/* — user administration. Admin manages accounts, not their data."""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps, users
from ..auth.capabilities import KNOWN_CAPABILITIES
from ..auth.config import load_auth_config
from ..auth.kc_admin import KCAdminError, KCRoleMissing
from ..llm.router import get_router
from ..http_errors import refusal
from ..services import (assistant_profile, chat_options, console_overview, console_settings, deployment_info, deployment_settings,
                        doc_content, docling_limits, inference_budget, inference_limits, model_router, model_settings, people, user_defaults,
                        web_search as web_search_service)
from ..services.console_settings import Section, SettingError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin"])
# users.project_limit is a Postgres INTEGER; larger values fail the UPDATE.
_PG_INT_MAX = 2**31 - 1


class UserPatchIn(BaseModel):
    status: str | None = None
    inference_daily_token_budget: int | None = None
    capabilities: list[str] | None = None
    project_limit: int | None = None


class UserEnrollIn(BaseModel):
    # House envelope style: unknown fields are a 422, not a silent ignore.
    model_config = ConfigDict(extra="forbid")
    email: str
    display_name: str | None = None
    username: str | None = Field(default=None, max_length=100)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    status: str = "pending"
    inference_daily_token_budget: int | None = None
    capabilities: list[str] = []


def _role_missing(e: KCRoleMissing) -> dict:
    """409 detail for a capability whose realm role the realm lacks."""
    return {"reason": "kc_role_missing", "role": e.role}


async def _other_active_admins(conn, user_id: str) -> int:
    """Count active admins OTHER than user_id — used to guard the last
    remaining active admin from being demoted/disabled and locking everyone
    out of admin (mirrors the rail delete_user already has).

    Counts by the `admin` CAPABILITY, not the legacy `role` column: the
    column is a best-effort mirror of capabilities (kept in sync by
    set_capabilities), but real admin access is gated on the capability
    alone (see deps._principal). Counting by column would let a desynced
    row — role='admin' without the capability — masquerade as a real admin
    and defeat this guard."""
    cur = await conn.execute(
        "SELECT count(*) FROM users WHERE 'admin' = ANY(capabilities) "
        "AND status='active' AND id <> %s",
        (user_id,),
    )
    return (await cur.fetchone())[0]


@router.get("/users")
async def list_all(
    _: deps.Principal = Depends(deps.require_admin), conn=Depends(deps.get_conn, scope="function")
):
    return await users.list_users(conn)


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
    kc=Depends(deps.get_kc_admin),
):
    # exclude_unset distinguishes "field absent" (untouched) from "explicit
    # null" (clear back to the deployment default) — required for the budget.
    data = body.model_dump(exclude_unset=True)

    target = await users.get_user(conn, user_id)  # need oidc_sub for KC calls
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    sub = target["oidc_sub"]

    if "status" in data:
        if data["status"] not in ("active", "pending", "disabled"):
            raise HTTPException(status_code=422, detail="bad status")
        if (
            data["status"] != "active"
            and "admin" in set(target["capabilities"])
            and target["status"] == "active"
            and await _other_active_admins(conn, user_id) == 0
        ):
            raise HTTPException(
                status_code=409, detail={"reason": "last_active_admin"}
            )
        await users.set_status(conn, user_id, data["status"])
        if kc is not None and sub:
            try:
                await kc.set_enabled(sub, data["status"] == "active")
            except Exception:
                logger.warning("KC set_enabled failed for %s", sub)
    if "capabilities" in data:
        caps = sorted(set(data["capabilities"]))
        if not set(caps).issubset(KNOWN_CAPABILITIES):
            raise HTTPException(status_code=422, detail="unknown capability")
        if (
            "admin" in set(target["capabilities"])
            and "admin" not in set(caps)
            and target["status"] == "active"
            and await _other_active_admins(conn, user_id) == 0
        ):
            raise HTTPException(
                status_code=409, detail={"reason": "last_active_admin"}
            )
        current = set(target["capabilities"])
        if kc is not None and sub:
            to_add = sorted(set(caps) - current)
            to_remove = sorted(current - set(caps))
            try:
                # Revoke first and hard-fail: a swallowed KC revoke would be
                # re-granted from the token at the user's next login (fail-open).
                if to_remove:
                    await kc.remove_realm_roles(sub, to_remove)
                if to_add:
                    await kc.assign_realm_roles(sub, to_add)
            except KCRoleMissing as e:
                raise HTTPException(status_code=409, detail=_role_missing(e))
            except Exception as e:
                logger.warning("KC role reconcile failed for %s: %s", sub, e)
                raise HTTPException(
                    status_code=502,
                    detail="Keycloak role sync failed; capabilities unchanged",
                )
        await users.set_capabilities(conn, user_id, caps)
    if "inference_daily_token_budget" in data:
        budget = data["inference_daily_token_budget"]
        if budget is not None and budget < 0:
            raise HTTPException(status_code=422, detail="budget must be >= 0")
        await users.set_inference_budget(conn, user_id, budget)
    if "project_limit" in data:
        limit = data["project_limit"]
        if limit is not None and not 0 <= limit <= _PG_INT_MAX:
            raise HTTPException(
                status_code=422, detail=f"project_limit must be between 0 and {_PG_INT_MAX}")
        await users.set_project_limit(conn, user_id, limit)
    updated = await users.get_user(conn, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.get("/projects")
async def list_projects(ownerless: bool = False,
                        _: deps.Principal = Depends(deps.require_admin),
                        conn=Depends(deps.get_conn, scope="function")):
    """Every project (A0 §9.5): owners, member and document counts, and
    whether it is ownerless (its last Owner was deleted) — the recovery list.
    Admins see owners' emails as labels' last resort, like the Users list."""
    cur = await conn.execute(
        "SELECT p.id, p.name, p.created_at, "
        "(SELECT count(*) FROM project_members m WHERE m.project_id = p.id), "
        "(SELECT count(*) FROM project_documents d "  # CURRENT
        "WHERE d.project_id = p.id AND d.verified AND d.is_current), "
        "COALESCE((SELECT json_agg(json_build_object('id', u.id, 'first_name', u.first_name, "
        "'last_name', u.last_name, 'display_name', u.display_name, 'username', u.username, "
        "'email', u.email) ORDER BY m.added_at, u.id) "
        "FROM project_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.project_id = p.id AND m.role = 'owner'), '[]'::json) "
        "FROM projects p "
        "WHERE NOT %s OR NOT EXISTS (SELECT 1 FROM project_members m "
        "WHERE m.project_id = p.id AND m.role = 'owner') "
        "ORDER BY p.created_at DESC, p.id", (ownerless,))
    out = []
    for r in await cur.fetchall():
        owners = [{"id": o["id"], "name": people.person_label(
            first_name=o["first_name"], last_name=o["last_name"], display_name=o["display_name"],
            username=o["username"], email=o["email"], show_email=True)} for o in r[5]]
        out.append({"id": str(r[0]), "name": r[1], "created_at": r[2], "member_count": r[3],
                    "doc_count": r[4], "owners": owners, "ownerless": not owners})
    return out


@router.post("/users", status_code=201)
async def enroll_user(
    body: UserEnrollIn,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
    kc=Depends(deps.get_kc_admin),
):
    """Pre-provision an account. When a Keycloak admin client is configured,
    this CREATES the Keycloak user, assigns its realm-role capabilities, and
    inserts an already-linked row — with compensation (delete the just-created
    Keycloak user) if the local insert fails afterward. Otherwise it falls
    back to today's app-only behavior: an unlinked row (oidc_sub NULL) that
    its owner claims on first verified-email login (resolver branch 2
    preserves the chosen role/status/budget)."""
    if body.status not in ("active", "pending", "disabled"):
        raise HTTPException(status_code=422, detail="bad status")
    caps = sorted(set(body.capabilities))
    if not set(caps).issubset(KNOWN_CAPABILITIES):
        raise HTTPException(status_code=422, detail="unknown capability")
    if body.inference_daily_token_budget is not None and body.inference_daily_token_budget < 0:
        raise HTTPException(status_code=422, detail="budget must be >= 0")

    if kc is None:
        try:
            user = await users.enroll_user(
                conn, email=body.email, display_name=body.display_name,
                status=body.status, capabilities=caps,
                inference_daily_token_budget=body.inference_daily_token_budget,
                username=body.username, first_name=body.first_name, last_name=body.last_name)
        except ValueError:
            raise HTTPException(status_code=409, detail="email already exists")
        return {"user": user, "onboarding": "manual"}

    try:
        exists = await kc.find_user_by_email(body.email)
    except KCAdminError as e:
        raise HTTPException(status_code=502, detail=f"Keycloak lookup failed: {e}")
    if exists:
        raise HTTPException(status_code=409, detail="email already exists in Keycloak")
    try:
        sub = await kc.create_user(email=body.email, display_name=body.display_name,
                                   email_verified=True, username=body.username,
                                   first_name=body.first_name, last_name=body.last_name)
    except KCAdminError as e:
        raise HTTPException(status_code=502, detail=f"Keycloak create failed: {e}")
    try:
        await kc.assign_realm_roles(sub, caps)
        onboarding, temp = "email", None
        if await kc.realm_smtp_configured():
            await kc.send_actions_email(sub, ["VERIFY_EMAIL", "UPDATE_PASSWORD"])
        else:
            temp = secrets.token_urlsafe(12)
            await kc.set_temp_password(sub, temp, temporary=True)
            onboarding = "temp_password"
        iss = load_auth_config(os.environ).oidc_issuer
        user = await users.enroll_linked_user(
            conn, iss=iss, sub=sub, email=body.email,
            display_name=body.display_name, capabilities=caps, status=body.status,
            inference_daily_token_budget=body.inference_daily_token_budget,
            username=body.username, first_name=body.first_name, last_name=body.last_name)
    except Exception as e:  # compensate: no orphaned Keycloak user
        try:
            await kc.delete_user(sub)
        except Exception:
            pass
        if isinstance(e, ValueError):
            raise HTTPException(status_code=409, detail="email already exists")
        if isinstance(e, KCRoleMissing):
            raise HTTPException(status_code=409, detail=_role_missing(e))
        raise HTTPException(status_code=502, detail=f"enroll failed: {e}")
    out = {"user": user, "onboarding": onboarding}
    if temp:
        out["temp_password"] = temp
    return out


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
    kc=Depends(deps.get_kc_admin),
):
    """Hard delete (admin-console spec §4): sessions, PATs, usage, library
    entries, project memberships and chat history all cascade (projects survive,
    ownerless if this was their last Owner — A0); content nobody else
    holds is then garbage-collected (A1 §3). Rails are server-side — the UI
    hiding them is cosmetic. Reasons are machine-readable in detail.reason."""
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")
    target = await users.get_user(conn, user_id)
    if target is None:
        # 404 for never-existed == already-deleted: no enumeration signal.
        raise HTTPException(status_code=404, detail="User not found")
    if target["id"] == principal.user_id:
        raise HTTPException(status_code=409, detail={"reason": "self"})
    if target["id"] == users.SEED_ADMIN_ID:
        raise HTTPException(
            status_code=409,
            detail={"reason": "seed_admin"},
        )
    if (
        "admin" in set(target["capabilities"])
        and target["status"] == "active"
        and await _other_active_admins(conn, user_id) == 0
    ):
        raise HTTPException(
            status_code=409, detail={"reason": "last_active_admin"}
        )
    # Collect the user's entry docs BEFORE the rows cascade away; GC runs
    # after — a cascade never runs app code.
    doc_ids = await doc_content.docs_referenced_by_user(conn, user_id)
    await users.delete_user(conn, user_id)
    if kc is not None and target["oidc_sub"]:
        try:
            await kc.delete_user(target["oidc_sub"])
        except Exception:
            logger.warning("KC delete failed for %s (app row already removed)",
                           target["oidc_sub"])
    for doc_id in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="user_deleted")
    return Response(status_code=204)


@router.get("/overview")
async def overview(
    _: deps.Principal = Depends(deps.require_admin), conn=Depends(deps.get_conn, scope="function"),
):
    """The Overview page's health checks: each part of the deployment, one line, never a hang."""
    checks = await console_overview.run_checks(conn, get_router())
    return {"checks": [c.to_json() for c in checks]}


@router.get("/models")
async def models_catalog(_: deps.Principal = Depends(deps.require_admin)):
    """What the console's Models section chooses from: every provider (an unreachable one is listed, with no
    models) and every chat model the environment allows. The console's own chat-model selection does not narrow
    this list; the admin picks from it. `detected` is what "Auto" means for a provider before any choice: the host
    rule alone, with no model name and no override."""
    llm = get_router()
    models, failed = await llm.list_models()
    offered = model_router.offered_chat_models(models, model_router.get_config().embed_model)
    return {
        "providers": [
            {"name": name, "kind": p.config.kind, "host": deployment_info.url_host(p.config.url),
             "detected": "local" if model_settings.is_local(p.config, "", {}) else "cloud",
             "reachable": name not in failed}
            for name, p in llm.providers.items()
        ],
        "models": [
            {"id": m.id, "provider": m.provider, "kind": m.kind, "name": m.name,
             "reported_window": m.capabilities.context_window,
             "cloud_name": m.kind == "ollama" and model_settings.is_ollama_cloud(m.name)}
            for m in offered
        ],
        "limits": {"num_ctx_max": inference_limits.get_limits().num_ctx_max},
    }


@router.get("/inference/config")
async def inference_config(
    _: deps.Principal = Depends(deps.require_admin), conn=Depends(deps.get_conn, scope="function"),
):
    """Read-only view of the effective inference config — why a model 422s,
    without shell access. Never includes API keys (spec §4.5). `summarize_model` keeps its name for old
    consumers; it is the reader the Models settings resolve to (the console's, else the environment's, else
    the chat model), worded as the Overview words it."""
    cfg = model_router.get_config()
    reader = (await model_settings.load_models_settings(conn, os.environ)).reader
    llm = get_router()
    models, failed = await llm.list_models()
    counts: dict[str, int] = {}
    for m in models:
        counts[m.provider] = counts.get(m.provider, 0) + 1
    return {
        "ollama_url": cfg.ollama_url,   # where embeddings run (spec §4.6)
        "timeout_s": cfg.timeout_s,
        "allowed_models": (
            list(cfg.allowed_models) if cfg.allowed_models is not None else None
        ),
        "summarize_model": model_settings.CHAT_READER if reader == "chat" else reader,
        "embed_model": cfg.embed_model,
        "daily_token_budget": cfg.daily_token_budget,
        "providers": [
            {"name": name, "kind": p.config.kind, "url_host": urlsplit(p.config.url).hostname,
             "models": None if name in failed else counts.get(name, 0)}
            for name, p in llm.providers.items()
        ],
    }


@router.get("/inference/usage")
async def inference_usage(
    days: int = 7,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    days = max(1, min(90, days))
    return await inference_budget.admin_usage(conn, days=days)


# ---------- deployment defaults for user preferences ----------

class UserDefaultsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]


@router.get("/defaults")
async def get_user_defaults(
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    return await user_defaults.admin_view(conn)


@router.put("/defaults")
async def put_user_defaults(
    body: UserDefaultsIn,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    try:
        await user_defaults.save(conn, body.values, updated_by=str(principal.user_id))
    except user_defaults.DefaultsError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await user_defaults.admin_view(conn)


@router.post("/defaults/{name}/apply-to-everyone")
async def apply_user_default_to_everyone(
    name: str,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    """Every browser drops its user's own choice for this setting the next
    time it loads the defaults."""
    try:
        await user_defaults.apply_to_everyone(conn, name, updated_by=str(principal.user_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown setting")
    return await user_defaults.admin_view(conn)


# ---------- the assistant profile (v2.4 Task A2) ----------

class AssistantProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=assistant_profile.PROFILE_MAX_CHARS)


async def _assistant_view(conn) -> dict:
    """What the admin console shows: the profile, where it comes from, and
    the full system message a sample turn would get (an indexed 10-page
    document, no pins, the tools that turn is offered under the deployment's
    web search mode), so an admin sees the profile next to the app's rules
    before users do."""
    from ..chat.context import today_line
    from ..chat.prompt import system_rules
    from ..chat.tools import ToolContext, available_tools
    from ..services.doc_search import ReadableDoc

    profile = await assistant_profile.load_profile(conn)
    sample = ReadableDoc("0" * 64, "Example.pdf", "indexed", 10)
    # The same call a real document chat makes (chat/orchestrator.py), so the preview can't show a combination
    # no turn has (web_search beside the offer sentence); a project chat's document_outline isn't offered.
    options = await chat_options.load_chat_options(conn)
    offered = available_tools(ToolContext("", sample, web_mode=options.web_search))
    preview = system_rules(sample, offered, has_pins=False, profile=profile.text,
                           today=today_line(datetime.now(timezone.utc), "UTC"))
    # `text` is the effective profile, from the file too: the editor starts
    # from it, and saving it unchanged is the console's job to skip.
    return {"text": profile.text, "source": profile.source,
            "fileConfigured": assistant_profile.file_path() is not None,
            "maxChars": assistant_profile.PROFILE_MAX_CHARS, "preview": preview,
            "warnings": assistant_profile.profile_warnings(profile.text)}


@router.get("/assistant")
async def get_assistant_profile(
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    return await _assistant_view(conn)


@router.put("/assistant")
async def put_assistant_profile(
    body: AssistantProfileIn,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    """Save the profile; empty text clears it (the file, or none, applies)."""
    await assistant_profile.save_profile(conn, body.text, updated_by=str(principal.user_id))
    return await _assistant_view(conn)


# ---------- chat options (C2.1 Task 13) ----------

class ChatOptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Required: null resets the setting (the environment, or "ask", applies again), so a body without the
    # field must not do it by accident.
    web_search: Literal["ask", "auto", "off"] | None


def _chat_options_view(options: chat_options.ChatOptions) -> dict:
    # `searxng`: false only when a deployer set SEARXNG_URL empty; the console then shows the control disabled.
    return {"web_search": options.web_search, "source": options.source,
            "searxng": bool(web_search_service.SEARXNG_URL)}


@router.get("/chat-options")
async def get_chat_options(
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    return _chat_options_view(await chat_options.load_chat_options(conn))


@router.put("/chat-options")
async def put_chat_options(
    body: ChatOptionsIn,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    """Save how web search asks; null resets it. Applies from the next chat message."""
    saved = await chat_options.save_chat_options(conn, body.web_search, updated_by=str(principal.user_id))
    return _chat_options_view(saved)


# ---------- console settings, one pair of routes for every section (admin console v2 spec §3) ----------

# section key -> (its settings, a function giving the read-only values shown beside them)
_SECTIONS: dict[str, tuple[Section, Callable[[], list[dict]]]] = {
    "deployment": (deployment_settings.SECTION, deployment_info.readonly_rows),
    "documents": (docling_limits.SECTION, docling_limits.info_rows),
    "models": (model_settings.SECTION, model_settings.info_rows),
}


class SectionChangesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # {setting: new value}; null resets that setting to the server's own (env or default).
    changes: dict[str, Any]


def _section(key: str) -> tuple[Section, Callable[[], list[dict]]]:
    if key not in _SECTIONS:
        raise refusal(404, "unknown_section", "No such settings section.")
    return _SECTIONS[key]


def _section_env(key: str) -> Mapping[str, str]:
    """The environment a section resolves against: the models section reads the reader's three-variable chain."""
    return model_settings.models_env(os.environ) if key == "models" else os.environ


async def _section_view(conn, key: str) -> dict:
    section, info = _section(key)
    values = await console_settings.load(conn, section, _section_env(key))
    settings = model_settings.view(values) if key == "models" else console_settings.view(section, values)
    return {"settings": settings, "info": info()}


@router.get("/settings/{section}")
async def get_settings_section(
    section: str,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    return await _section_view(conn, section)


@router.put("/settings/{section}")
async def put_settings_section(
    section: str,
    body: SectionChangesIn,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn, scope="function"),
):
    """Save some settings of one section; null resets one. One refused value saves nothing. Applies at once."""
    spec, _info = _section(section)
    try:
        await console_settings.save(conn, spec, body.changes, updated_by=str(principal.user_id),
                                    env=_section_env(section))
    except SettingError as e:
        raise refusal(422, "invalid_setting", str(e), setting=e.name) from None
    return await _section_view(conn, section)
