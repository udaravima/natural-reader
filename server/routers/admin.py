"""/v1/admin/* — user administration. Admin manages accounts, not their data."""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from ..auth import deps, users
from ..auth.capabilities import KNOWN_CAPABILITIES
from ..auth.config import load_auth_config
from ..auth.kc_admin import KCAdminError
from ..services import inference_budget, model_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin"])


class UserPatchIn(BaseModel):
    status: str | None = None
    role: str | None = None
    inference_daily_token_budget: int | None = None
    capabilities: list[str] | None = None


class UserEnrollIn(BaseModel):
    # House envelope style: unknown fields are a 422, not a silent ignore.
    model_config = ConfigDict(extra="forbid")
    email: str
    display_name: str | None = None
    role: str = "member"
    status: str = "pending"
    inference_daily_token_budget: int | None = None
    capabilities: list[str] = []


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
    if "role" in data:
        if data["role"] not in ("admin", "member"):
            raise HTTPException(status_code=422, detail="bad role")
        await users.set_role(conn, user_id, data["role"])
    if "inference_daily_token_budget" in data:
        budget = data["inference_daily_token_budget"]
        if budget is not None and budget < 0:
            raise HTTPException(status_code=422, detail="budget must be >= 0")
        await users.set_inference_budget(conn, user_id, budget)
    updated = await users.get_user(conn, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


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
                inference_daily_token_budget=body.inference_daily_token_budget)
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
                                   email_verified=True)
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
            inference_daily_token_budget=body.inference_daily_token_budget)
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
    """Hard delete (admin-console spec §4): sessions, PATs, usage, documents
    (+chunks) and chat history all cascade. Rails are server-side — the UI
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
    if target["role"] == "admin" and target["status"] == "active":
        cur = await conn.execute(
            "SELECT count(*) FROM users "
            "WHERE role='admin' AND status='active' AND id <> %s",
            (user_id,),
        )
        if (await cur.fetchone())[0] == 0:
            raise HTTPException(
                status_code=409, detail={"reason": "last_active_admin"}
            )
    # PDF sweep: collect the user's stored files BEFORE the rows cascade away.
    # Best-effort — a failed unlink after the rows are gone is only a disk
    # leak, never a correctness issue.
    cur = await conn.execute(
        "SELECT pdf_path FROM documents WHERE user_id=%s AND pdf_path IS NOT NULL",
        (user_id,),
    )
    pdf_paths = [r[0] for r in await cur.fetchall()]
    await users.delete_user(conn, user_id)
    if kc is not None and target["oidc_sub"]:
        try:
            await kc.delete_user(target["oidc_sub"])
        except Exception:
            logger.warning("KC delete failed for %s (app row already removed)",
                           target["oidc_sub"])
    for raw in pdf_paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove PDF %s for deleted user", raw)
    return Response(status_code=204)


@router.get("/inference/config")
async def inference_config(
    _: deps.Principal = Depends(deps.require_admin),
):
    """Read-only view of the effective model-router config — why a model
    422s, without shell access. get_config() has no secrets in it."""
    cfg = model_router.get_config()
    return {
        "ollama_url": cfg.ollama_url,
        "timeout_s": cfg.timeout_s,
        "allowed_models": (
            list(cfg.allowed_models) if cfg.allowed_models is not None else None
        ),
        "summarize_model": cfg.summarize_model,
        "embed_model": cfg.embed_model,
        "daily_token_budget": cfg.daily_token_budget,
    }


@router.get("/inference/usage")
async def inference_usage(
    days: int = 7,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
):
    days = max(1, min(90, days))
    return await inference_budget.admin_usage(conn, days=days)
