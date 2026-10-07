"""/v1/admin/* — user administration. Admin manages accounts, not their data."""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from urllib.parse import urlsplit

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps, users
from ..auth.capabilities import KNOWN_CAPABILITIES
from ..auth.config import load_auth_config
from ..auth.kc_admin import KCAdminError
from ..llm.router import get_router
from ..services import assistant_profile, doc_content, inference_budget, model_router, people

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin"])


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
    _: deps.Principal = Depends(deps.require_admin), conn=Depends(deps.get_conn)
):
    return await users.list_users(conn)


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
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
        if limit is not None and limit < 0:
            raise HTTPException(status_code=422, detail="project_limit must be >= 0")
        await users.set_project_limit(conn, user_id, limit)
    updated = await users.get_user(conn, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.get("/projects")
async def list_projects(ownerless: bool = False,
                        _: deps.Principal = Depends(deps.require_admin),
                        conn=Depends(deps.get_conn)):
    """Every project (A0 §9.5): owners, member and document counts, and
    whether it is ownerless (its last Owner was deleted) — the recovery list.
    Admins see owners' emails as labels' last resort, like the Users list."""
    cur = await conn.execute(
        "SELECT p.id, p.name, p.created_at, "
        "(SELECT count(*) FROM project_members m WHERE m.project_id = p.id), "
        "(SELECT count(*) FROM project_documents d WHERE d.project_id = p.id AND d.verified), "
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
    conn=Depends(deps.get_conn),
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
        raise HTTPException(status_code=502, detail=f"enroll failed: {e}")
    out = {"user": user, "onboarding": onboarding}
    if temp:
        out["temp_password"] = temp
    return out


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
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


@router.get("/inference/config")
async def inference_config(
    _: deps.Principal = Depends(deps.require_admin),
):
    """Read-only view of the effective inference config — why a model 422s,
    without shell access. Never includes API keys (spec §4.5)."""
    cfg = model_router.get_config()
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
        "summarize_model": cfg.summarize_model,
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
    conn=Depends(deps.get_conn),
):
    days = max(1, min(90, days))
    return await inference_budget.admin_usage(conn, days=days)


# ---------- the assistant profile (v2.4 Task A2) ----------

class AssistantProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=assistant_profile.PROFILE_MAX_CHARS)


async def _assistant_view(conn) -> dict:
    """What the admin console shows: the profile, where it comes from, and
    the full system message a sample turn would get (an indexed 10-page
    document, every tool, no pins), so an admin sees the profile next to the
    app's rules before users do."""
    from ..chat.context import today_line
    from ..chat.prompt import system_rules
    from ..chat.tools import REGISTRY
    from ..services.doc_search import ReadableDoc

    profile = await assistant_profile.load_profile(conn)
    sample = ReadableDoc("0" * 64, "Example.pdf", "indexed", 10)
    preview = system_rules(sample, REGISTRY, has_pins=False, profile=profile.text,
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
    conn=Depends(deps.get_conn),
):
    return await _assistant_view(conn)


@router.put("/assistant")
async def put_assistant_profile(
    body: AssistantProfileIn,
    principal: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
):
    """Save the profile; empty text clears it (the file, or none, applies)."""
    await assistant_profile.save_profile(conn, body.text, updated_by=str(principal.user_id))
    return await _assistant_view(conn)
