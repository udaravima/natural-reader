"""Admin lifecycle endpoints — enroll, hard delete (with rails + content GC),
and the read-only inference config view (admin-console spec §7 items 1-10)."""
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import (
    SEED_ADMIN_ID,
    get_user,
    resolve_or_provision_user,
)
from server.routers import admin as admin_router
from server.tests import seed


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    # Deterministic app-only enroll path: these lifecycle tests exercise the
    # fallback behavior, not live Keycloak provisioning (see test_admin_router.py
    # for the Keycloak-integrated enroll tests with a fake KC admin client).
    app.dependency_overrides[deps.get_kc_admin] = lambda: None
    app.include_router(admin_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _admin(db_conn):
    """Claim the seed admin (first-login path) and build its Principal."""
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    p = deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")
    return admin, p


async def _make_user(db_conn, email, *, role="member", status="pending", capabilities=None):
    """A second user, directly shaped (PATCH exists but raw SQL is less
    indirection here — these tests are about DELETE, not PATCH).

    Real admin access is gated on the `admin` CAPABILITY, not the `role`
    column (see admin.py's _other_active_admins) — so role="admin" here
    also grants the capability by default, to keep these "make this user an
    admin" call sites meaningful. Pass capabilities explicitly to construct
    a desynced row (role='admin' with no capability) instead."""
    caps = capabilities if capabilities is not None else (["admin"] if role == "admin" else [])
    cur = await db_conn.execute(
        "INSERT INTO users (email, display_name, role, status, capabilities) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (email, email, role, status, caps),
    )
    return str((await cur.fetchone())[0])


async def _count(db_conn, sql, params=()):
    cur = await db_conn.execute(sql, params)
    return (await cur.fetchone())[0]


# ---------- DELETE /v1/admin/users/{id} ----------

async def test_delete_user_happy_path_cascades(db_conn):
    from server.services import inference_budget as ib

    admin, p = await _admin(db_conn)
    victim = await _make_user(db_conn, "b@x.io", status="active")

    # Rows in every user-owned table that must cascade.
    await db_conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (%s, %s, now() + interval '1 day')",
        ("sess1", victim),
    )
    await db_conn.execute(
        "INSERT INTO personal_access_tokens (user_id, name, token_hash) VALUES (%s, %s, %s)",
        (victim, "t", "hash1"),
    )
    await ib.record_usage(db_conn, victim, 10, 5)
    await db_conn.execute(
        "INSERT INTO chat_sessions (id, user_id) VALUES (%s, %s)", ("cs1", victim)
    )
    await seed.seed_doc(db_conn, "d1", victim, file_name="d.pdf", file_type="pdf")

    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{victim}")
    assert r.status_code == 204

    assert await get_user(db_conn, victim) is None
    for table in (
        "sessions", "personal_access_tokens", "inference_usage",
        "chat_sessions", "library_entries", "documents",
    ):
        assert await _count(db_conn, f"SELECT count(*) FROM {table}") == 0, table


async def test_delete_self_rejected(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{admin['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "self"


async def test_delete_last_active_admin_rejected(db_conn):
    admin, p = await _admin(db_conn)
    victim = await _make_user(db_conn, "b@x.io", role="admin", status="active")
    # The seed admin (the only OTHER active admin) is disabled, making the
    # victim the last active admin.
    await db_conn.execute(
        "UPDATE users SET status='disabled' WHERE id=%s", (SEED_ADMIN_ID,)
    )
    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{victim}")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "last_active_admin"


async def test_delete_other_admin_allowed_when_admin_remains(db_conn):
    admin, p = await _admin(db_conn)
    victim = await _make_user(db_conn, "b@x.io", role="admin", status="active")
    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{victim}")
    assert r.status_code == 204


async def test_delete_seed_admin_rejected(db_conn):
    admin, p = await _admin(db_conn)
    # The acting admin must NOT be the seed claimant, or the `self` rail
    # (checked first) fires instead of the seed rail.
    other = await _make_user(db_conn, "b@x.io", role="admin", status="active")
    acting = deps.Principal(user_id=other, email="b@x.io", role="admin")
    async with _client(_app(db_conn, acting)) as client:
        r = await client.delete(f"/v1/admin/users/{SEED_ADMIN_ID}")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "seed_admin"


async def test_delete_missing_user_404(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_delete_user_gcs_sole_held_content(db_conn, tmp_path):
    # A1 §3: deleting a user drops their entries and (until A0) placements in
    # projects they own; content goes only if nobody else still holds it.
    admin, p = await _admin(db_conn)
    victim = await _make_user(db_conn, "b@x.io", status="active")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'Theirs') RETURNING id",
        (victim,))
    victims_project = str((await cur.fetchone())[0])

    files = {}
    for name in ("sole", "co_held", "admins"):
        files[name] = tmp_path / f"{name}.pdf"
        files[name].write_bytes(name.encode())
    await seed.seed_doc(db_conn, "d1", victim, bytes_path=files["sole"])
    await seed.seed_doc(db_conn, "d2", victim, bytes_path=files["co_held"])
    await seed.share_doc(db_conn, "d2", victim, admin["id"])  # someone else holds it too
    await seed.seed_doc(db_conn, "d3", None, project_ids=[victims_project])  # placement only
    await seed.seed_doc(db_conn, "d4", admin["id"], bytes_path=files["admins"])

    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{victim}")
    assert r.status_code == 204

    cur = await db_conn.execute(
        "SELECT doc_id FROM documents WHERE doc_id IN ('d1','d2','d3','d4')")
    assert {r[0] for r in await cur.fetchall()} == {"d2", "d4"}
    assert not files["sole"].exists()
    assert files["co_held"].exists()  # the other holder's copy keeps its bytes
    assert files["admins"].exists()   # other users' content is untouched


# ---------- GET /v1/admin/inference/config ----------

async def test_config_admin_view(db_conn, monkeypatch):
    monkeypatch.setenv("INFERENCE_MODELS", "m1, m2")
    monkeypatch.setenv("INFERENCE_TIMEOUT_S", "123")
    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "5000")
    monkeypatch.setenv("SUMMARIZE_MODEL", "sum-model")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-model")
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/config")
    assert r.status_code == 200
    body = r.json()
    assert body["allowed_models"] == ["m1", "m2"]
    assert body["timeout_s"] == 123.0
    assert body["daily_token_budget"] == 5000
    assert body["summarize_model"] == "sum-model"
    assert body["embed_model"] == "embed-model"


async def test_config_unset_allowlist_renders_null(db_conn, monkeypatch):
    for var in ("INFERENCE_MODELS", "INFERENCE_DAILY_TOKEN_BUDGET"):
        monkeypatch.delenv(var, raising=False)
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/config")
    assert r.status_code == 200
    assert r.json()["allowed_models"] is None
    assert r.json()["daily_token_budget"] is None


async def test_config_member_forbidden(db_conn):
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/config")
    assert r.status_code == 403


# ---------- POST /v1/admin/users (enrollment) ----------

async def test_enroll_creates_unlinked_row(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.post(
            "/v1/admin/users",
            json={
                "email": "new@x.io",
                "display_name": "New Person",
                "status": "pending",
                "inference_daily_token_budget": 1234,
            },
        )
    assert r.status_code == 201
    body = r.json()
    assert body["onboarding"] == "manual"
    user = body["user"]
    assert user["email"] == "new@x.io"
    assert user["oidc_sub"] is None
    assert user["role"] == "member"
    assert user["status"] == "pending"
    assert user["inference_daily_token_budget"] == 1234
    assert user["display_name"] == "New Person"


async def test_enroll_defaults_minimal_body(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.post("/v1/admin/users", json={"email": "new@x.io"})
    assert r.status_code == 201
    body = r.json()
    assert body["onboarding"] == "manual"
    user = body["user"]
    assert user["role"] == "member"
    assert user["status"] == "pending"
    assert user["inference_daily_token_budget"] is None


async def test_enroll_duplicate_email_409(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.post("/v1/admin/users", json={"email": admin["email"]})
    assert r.status_code == 409


async def test_enroll_member_forbidden(db_conn):
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        r = await client.post("/v1/admin/users", json={"email": "new@x.io"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        # `role` is no longer independently validated by the enroll endpoint —
        # `capabilities` (mapped to Keycloak realm roles / KNOWN_CAPABILITIES)
        # is now the validated axis, so that case is replaced below.
        {"email": "n@x.io", "capabilities": ["superadmin"]},
        {"email": "n@x.io", "status": "banned"},
        {"email": "n@x.io", "inference_daily_token_budget": -1},
        {"email": "n@x.io", "surprise": True},
    ],
)
async def test_enroll_rejects_bad_input(db_conn, payload):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.post("/v1/admin/users", json=payload)
    assert r.status_code == 422


# ---------- enroll → login claim (spec §7 item 10) ----------

async def test_enrolled_row_claimed_by_verified_login(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.post(
            "/v1/admin/users",
            json={
                "email": "new@x.io",
                "status": "active",
                "inference_daily_token_budget": 777,
            },
        )
    enrolled_id = r.json()["user"]["id"]

    claimed = await resolve_or_provision_user(
        db_conn, iss="iss2", sub="sub2", email="new@x.io", email_verified=True
    )
    assert claimed["id"] == enrolled_id
    assert claimed["oidc_sub"] == "sub2"
    assert claimed["role"] == "member"
    assert claimed["status"] == "active"
    assert claimed["inference_daily_token_budget"] == 777


async def test_enrolled_row_not_claimed_by_unverified_login(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        await client.post("/v1/admin/users", json={"email": "new@x.io"})
    with pytest.raises(ValueError):
        await resolve_or_provision_user(
            db_conn, iss="iss2", sub="sub2", email="new@x.io", email_verified=False
        )
