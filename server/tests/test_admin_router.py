from datetime import timedelta

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router
from server.services import deployment_info


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_admin_lists_and_activates(db_conn):
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    pending = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")
    async with _client(_app(db_conn, p)) as client:
        assert len((await client.get("/v1/admin/users")).json()) == 2
        r = await client.patch(
            f"/v1/admin/users/{pending['id']}", json={"status": "active"}
        )
        assert r.status_code == 200 and r.json()["status"] == "active"


async def test_member_forbidden(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        assert (await client.get("/v1/admin/users")).status_code == 403


async def _admin(db_conn):
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    return admin, deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")


async def test_inference_usage_admin_only(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/usage")
        assert r.status_code == 403


async def test_admin_inference_usage_returns_rows(db_conn):
    from server.services import inference_budget as ib

    admin, p = await _admin(db_conn)
    await ib.record_usage(db_conn, admin["id"], 10, 5, model="ollama:test", kind="chat", estimated=False)
    async with _client(_app(db_conn, p)) as client:
        rows = (await client.get("/v1/admin/inference/usage?days=1")).json()
    assert len(rows) == 1
    assert rows[0]["tokens"] == 15
    assert rows[0]["email"] == admin["email"]


async def test_admin_inference_usage_clamps_days(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/usage?days=99999")
    assert r.status_code == 200  # clamped to 90, not an error


# ---------- GET /v1/admin/inference/usage?group=...: usage by model, person and day ----------

async def _usage_world(db_conn, monkeypatch, marks=None):
    """Two providers on the router (a host-local `ollama`, a cloud-hosted `openrouter`) and, optionally, the
    Models section's provider_locality marks."""
    from server.services import console_settings, model_settings
    admin, p = await _admin(db_conn)
    router = _CatalogRouter([_CatalogProvider("ollama", "ollama", "http://ollama:11434"),
                             _CatalogProvider("openrouter", "openai", "https://openrouter.example.com/api/v1")], [])
    monkeypatch.setattr(admin_router, "get_router", lambda: router)
    if marks:
        await console_settings.save(db_conn, model_settings.SECTION, {"provider_locality": marks},
                                    updated_by=str(admin["id"]))
    return admin, p


async def _usage(db_conn, p, query):
    async with _client(_app(db_conn, p)) as client:
        return await client.get(f"/v1/admin/inference/usage?{query}")


async def _use(db_conn, user, model, kind, p=10, e=5, estimated=False):
    from server.services import inference_budget as ib
    await ib.record_usage(db_conn, user["id"], p, e, model=model, kind=kind, estimated=estimated)


async def test_usage_group_model_tags(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch, marks={"ollama": "local"})
    await _use(db_conn, admin, "ollama:gemma4:e4b", "chat", 100, 50)
    await _use(db_conn, admin, "openrouter:some/model", "chat", 10, 5)
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, (SELECT max(day) FROM inference_usage), '', '', 1, 1, 1)", (admin["id"],))
    r = await _usage(db_conn, p, "group=model")
    assert r.status_code == 200
    assert [(x["model"], x["tag"], x["tokens"]) for x in r.json()] == [
        ("ollama:gemma4:e4b", "local", 150), ("openrouter:some/model", "cloud", 15), ("", "unknown", 2)]


async def test_usage_group_model_follows_the_provider_mark(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch, marks={"ollama": "cloud", "openrouter": "local"})
    await _use(db_conn, admin, "ollama:m", "chat")
    await _use(db_conn, admin, "openrouter:m", "chat")
    tags = {x["model"]: x["tag"] for x in (await _usage(db_conn, p, "group=model")).json()}
    assert tags == {"ollama:m": "cloud", "openrouter:m": "local"}


async def test_usage_group_model_removed_provider_unknown(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch)
    await _use(db_conn, admin, "gone:m", "chat")
    await _use(db_conn, admin, "gemma4:e4b", "reader")    # a bare reader name the router could not place
    r = await _usage(db_conn, p, "group=model")
    assert r.status_code == 200
    assert {x["model"]: x["tag"] for x in r.json()} == {"gone:m": "unknown", "gemma4:e4b": "unknown"}


async def test_usage_group_user_with_model_filter(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await _use(db_conn, admin, "ollama:a", "chat", 10, 0)
    await _use(db_conn, admin, "ollama:b", "chat", 1000, 0)
    await _use(db_conn, other, "ollama:a", "chat", 40, 0)
    await _use(db_conn, other, "ollama:b", "chat", 2000, 0)
    rows = (await _usage(db_conn, p, "group=user&model=ollama:a")).json()
    assert [(x["email"], x["user_id"], x["tokens"]) for x in rows] == [
        ("b@x.io", str(other["id"]), 40), ("a@x.io", str(admin["id"]), 10)]


async def test_usage_user_filter_group_model(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await _use(db_conn, admin, "ollama:a", "chat", 10, 0)
    await _use(db_conn, admin, "ollama:b", "chat", 30, 0)
    await _use(db_conn, other, "ollama:a", "chat", 500, 0)
    rows = (await _usage(db_conn, p, f"group=model&user_id={admin['id']}")).json()
    assert [(x["model"], x["tokens"]) for x in rows] == [("ollama:b", 30), ("ollama:a", 10)]


async def test_usage_model_filter_empty_matches_unknown(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch)
    await _use(db_conn, admin, "ollama:a", "chat")
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, (SELECT max(day) FROM inference_usage), '', '', 7, 0, 1)", (admin["id"],))
    rows = (await _usage(db_conn, p, "group=model&model=")).json()
    assert [(x["model"], x["tag"], x["tokens"]) for x in rows] == [("", "unknown", 7)]


async def test_usage_group_day(db_conn, monkeypatch):
    from server.services import inference_budget as ib
    admin, p = await _usage_world(db_conn, monkeypatch)
    today = await ib.today(db_conn)
    await _use(db_conn, admin, "ollama:a", "chat", 10, 5)
    await _use(db_conn, admin, "ollama:b", "reader", 1, 1)
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, %s, 'ollama:a', 'chat', 100, 0, 1)", (admin["id"], today - timedelta(days=1)))
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, %s, 'ollama:a', 'chat', 9, 0, 1)", (admin["id"], today - timedelta(days=8)))   # outside 7 days
    rows = (await _usage(db_conn, p, "group=day&days=7")).json()
    assert [(x["day"], x["tokens"], x["requests"]) for x in rows] == [
        (today.isoformat(), 17, 2), ((today - timedelta(days=1)).isoformat(), 100, 1)]
    assert set(rows[0]) == {"day", "prompt_tokens", "eval_tokens", "tokens", "requests", "chat_tokens",
                            "reader_tokens", "estimated_tokens"}


async def test_usage_chat_reader_split(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch)
    await _use(db_conn, admin, "ollama:a", "chat", 10, 5, estimated=True)
    await _use(db_conn, admin, "ollama:a", "reader", 3, 2)
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, (SELECT max(day) FROM inference_usage), 'ollama:a', '', 100, 0, 1)", (admin["id"],))
    (row,) = (await _usage(db_conn, p, "group=model")).json()
    assert (row["tokens"], row["chat_tokens"], row["reader_tokens"]) == (120, 15, 5)   # '' counts in tokens only
    assert (row["prompt_tokens"], row["eval_tokens"], row["requests"], row["estimated_tokens"]) == (113, 7, 3, 15)
    assert all(type(row[k]) is int for k in ("tokens", "chat_tokens", "reader_tokens", "estimated_tokens"))


async def test_usage_bad_group_422(db_conn, monkeypatch):
    _, p = await _usage_world(db_conn, monkeypatch)
    assert (await _usage(db_conn, p, "group=nope")).status_code == 422


async def test_usage_bad_user_id_422(db_conn, monkeypatch):
    _, p = await _usage_world(db_conn, monkeypatch)
    assert (await _usage(db_conn, p, "user_id=not-a-uuid")).status_code == 422


async def test_usage_default_shape_unchanged(db_conn, monkeypatch):
    admin, p = await _usage_world(db_conn, monkeypatch)
    await _use(db_conn, admin, "ollama:a", "chat", 10, 5)
    await _use(db_conn, admin, "ollama:b", "reader", 1, 1)
    plain = (await _usage(db_conn, p, "days=7")).json()
    explicit = (await _usage(db_conn, p, "days=7&group=user_day")).json()
    assert plain == explicit
    assert len(plain) == 1 and set(plain[0]) == {"email", "user_id", "day", "prompt_tokens", "eval_tokens",
                                                 "requests", "tokens"}
    assert (plain[0]["tokens"], plain[0]["requests"], plain[0]["user_id"]) == (17, 2, str(admin["id"]))


async def test_usage_grouped_requires_admin(db_conn, monkeypatch):
    await _usage_world(db_conn, monkeypatch)
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    for g in ("user_day", "model", "user", "day"):
        assert (await _usage(db_conn, p, f"group={g}")).status_code == 403


async def test_patch_sets_and_clears_budget(db_conn):
    admin, p = await _admin(db_conn)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="c@x.io")

    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": 1000}
        )
        assert r.status_code == 200
        # explicit null clears back to the deployment default
        r = await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": None}
        )
        assert r.status_code == 200
        # an absent field leaves the column untouched
        r = await client.patch(f"/v1/admin/users/{other['id']}", json={})
        assert r.status_code == 200

    from server.auth.users import get_user

    assert (await get_user(db_conn, other["id"]))["inference_daily_token_budget"] is None


async def test_patch_budget_round_trip(db_conn):
    admin, p = await _admin(db_conn)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="c@x.io")

    async with _client(_app(db_conn, p)) as client:
        await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": 5000}
        )
    from server.auth.users import get_user

    assert (await get_user(db_conn, other["id"]))["inference_daily_token_budget"] == 5000


async def test_patch_rejects_negative_budget(db_conn):
    admin, p = await _admin(db_conn)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="c@x.io")
    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": -5}
        )
    assert r.status_code == 422


class _FakeKC:
    def __init__(self, smtp=False, existing=None):
        self.smtp, self.existing = smtp, (existing or set())
        self.created, self.roles, self.temp, self.emailed, self.deleted = [], {}, {}, [], []
    async def find_user_by_email(self, email): return "kc-x" if email in self.existing else None
    async def create_user(self, *, email, display_name=None, email_verified=False, username=None,
                          first_name=None, last_name=None):
        self.created.append(email); return f"sub-{email}"
    async def assign_realm_roles(self, sub, names): self.roles[sub] = list(names)
    async def set_temp_password(self, sub, pw, *, temporary=True): self.temp[sub] = pw
    async def send_actions_email(self, sub, actions): self.emailed.append(sub)
    async def realm_smtp_configured(self): return self.smtp
    async def delete_user(self, sub): self.deleted.append(sub)


def _app_kc(db_conn, principal, kc):
    from fastapi import FastAPI
    app = FastAPI()
    async def _conn():  # noqa
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.dependency_overrides[deps.get_kc_admin] = lambda: kc
    app.include_router(admin_router.router)
    return app


async def test_enroll_creates_linked_kc_user_temp_password(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKC(smtp=False)
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={
            "email": "new@x.io", "capabilities": ["reader", "chat"], "status": "active"})
    assert r.status_code == 201
    body = r.json()
    assert body["onboarding"] == "temp_password" and body["temp_password"]
    assert body["user"]["oidc_sub"] == "sub-new@x.io"
    assert set(body["user"]["capabilities"]) == {"reader", "chat"}
    # assign_realm_roles receives `caps` after sorted(set(...)) canonicalization
    # (same treatment as storage), so compare unordered rather than by position.
    assert set(kc.roles["sub-new@x.io"]) == {"reader", "chat"}


async def test_enroll_sends_invite_when_smtp(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKC(smtp=True)
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={"email": "e@x.io", "capabilities": []})
    assert r.json()["onboarding"] == "email" and "temp_password" not in r.json()
    assert kc.emailed == ["sub-e@x.io"]


async def test_enroll_conflicts_when_email_in_kc(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKC(existing={"dup@x.io"})
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={"email": "dup@x.io", "capabilities": []})
    assert r.status_code == 409


async def test_enroll_falls_back_when_kc_absent(db_conn):
    _, p = await _admin(db_conn)
    async with _client(_app_kc(db_conn, p, None)) as c:
        r = await c.post("/v1/admin/users", json={"email": "f@x.io", "capabilities": ["reader"]})
    body = r.json()
    assert r.status_code == 201 and body["onboarding"] == "manual"
    assert body["user"]["oidc_sub"] is None
    assert set(body["user"]["capabilities"]) == {"reader"}


class _FakeKCBoom(_FakeKC):
    """A KC fake that succeeds through create_user but fails on the very next
    call — exercises the "ANY post-create failure compensates" safety
    property (a generic, non-KCAdminError exception, to prove the except
    Exception catch-all in the router's compensation block is reached, not
    just the KCAdminError-specific paths)."""
    async def assign_realm_roles(self, sub, names):
        raise Exception("boom")


async def test_enroll_generic_post_create_failure_compensates(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKCBoom()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={"email": "boom@x.io", "capabilities": []})
    assert r.status_code == 502
    # The compensation path deleted the just-created Keycloak user — no
    # orphan left behind by the failed enroll.
    assert kc.deleted == ["sub-boom@x.io"]


async def test_enroll_local_conflict_compensates(db_conn):
    _, p = await _admin(db_conn)
    # A row already owns this email app-side (but NOT in Keycloak, per the
    # fake's empty `existing` set below) — enroll_linked_user's INSERT hits
    # the UNIQUE(email) constraint -> ValueError, AFTER kc.create_user already
    # minted a Keycloak identity for it.
    await resolve_or_provision_user(db_conn, iss="i", sub="pre", email="dupe@x.io")
    kc = _FakeKC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={"email": "dupe@x.io", "capabilities": []})
    assert r.status_code == 409
    # Compensation still fires for a LOCAL (app-side) failure, not just a
    # Keycloak-side one — the just-created Keycloak user is deleted.
    assert kc.deleted == ["sub-dupe@x.io"]


async def test_patch_capabilities_reconciles_kc_and_db(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user, get_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-1", email="c@x.io",
                                 capabilities=["reader"], status="active")

    class _KC(_FakeKC):
        def __init__(self): super().__init__(); self.assigned=[]; self.removed=[]
        async def assign_realm_roles(self, sub, names): self.assigned.append((sub, list(names)))
        async def remove_realm_roles(self, sub, names): self.removed.append((sub, list(names)))
    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.patch(f"/v1/admin/users/{u['id']}",
                          json={"capabilities": ["reader", "chat"]})
    assert r.status_code == 200
    assert set((await get_user(db_conn, u["id"]))["capabilities"]) == {"reader", "chat"}
    assert kc.assigned == [("sub-1", ["chat"])] and kc.removed == []


async def test_patch_accepts_the_docling_capability(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user, get_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-d", email="d@x.io",
                                 capabilities=["reader"], status="active")

    class _KC(_FakeKC):
        def __init__(self): super().__init__(); self.assigned=[]
        async def assign_realm_roles(self, sub, names): self.assigned.append((sub, list(names)))
    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.patch(f"/v1/admin/users/{u['id']}", json={"capabilities": ["reader", "docling"]})
    assert r.status_code == 200
    assert set((await get_user(db_conn, u["id"]))["capabilities"]) == {"reader", "docling"}
    assert kc.assigned == [("sub-d", ["docling"])]


class _FakeKCNoDocling(_FakeKC):
    """A realm that predates the `docling` role: granting it fails at the lookup."""
    def __init__(self):
        super().__init__(); self.assigned = []
    async def assign_realm_roles(self, sub, names):
        from server.auth.kc_admin import KCRoleMissing
        if "docling" in names:
            raise KCRoleMissing("docling")
        self.assigned.append((sub, list(names)))
    async def remove_realm_roles(self, sub, names): pass


async def test_patch_docling_without_the_realm_role_says_which_role(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user, get_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-m", email="m@x.io",
                                 capabilities=["reader"], status="active")
    async with _client(_app_kc(db_conn, p, _FakeKCNoDocling())) as c:
        r = await c.patch(f"/v1/admin/users/{u['id']}", json={"capabilities": ["reader", "docling"]})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "kc_role_missing", "role": "docling"}
    assert (await get_user(db_conn, u["id"]))["capabilities"] == ["reader"]


async def test_enroll_with_docling_without_the_realm_role_compensates(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKCNoDocling()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={
            "email": "n@x.io", "capabilities": ["reader", "docling"], "status": "active"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "kc_role_missing", "role": "docling"}
    assert kc.deleted == ["sub-n@x.io"]


async def test_disable_propagates_enabled_false(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-2", email="d@x.io",
                                 capabilities=["reader"], status="active")
    class _KC(_FakeKC):
        def __init__(self): super().__init__(); self.enabled=[]
        async def set_enabled(self, sub, enabled): self.enabled.append((sub, enabled))
    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        await c.patch(f"/v1/admin/users/{u['id']}", json={"status": "disabled"})
    assert kc.enabled == [("sub-2", False)]


async def test_delete_removes_kc_user(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-3", email="g@x.io",
                                 capabilities=["reader"], status="active")
    kc = _FakeKC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.delete(f"/v1/admin/users/{u['id']}")
    assert r.status_code == 204 and kc.deleted == ["sub-3"]


async def test_disable_survives_kc_failure(db_conn):
    """KC set_enabled failure must NOT 500 the PATCH; DB status change persists."""
    from server.auth.users import enroll_linked_user, get_user
    admin, p = await _admin(db_conn)
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-x", email="x@x.io",
                                 capabilities=["reader"], status="active")

    class _KC(_FakeKC):
        async def set_enabled(self, sub, enabled):
            raise Exception("kc down")

    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.patch(f"/v1/admin/users/{u['id']}", json={"status": "disabled"})
    assert r.status_code == 200
    assert (await get_user(db_conn, u["id"]))["status"] == "disabled"


async def test_delete_survives_kc_failure(db_conn):
    """KC delete_user failure must NOT 500 the DELETE; app row is still gone."""
    from server.auth.users import enroll_linked_user, get_user
    admin, p = await _admin(db_conn)
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-y", email="y@x.io",
                                 capabilities=["reader"], status="active")

    class _KC(_FakeKC):
        async def delete_user(self, sub):
            raise Exception("kc down")

    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.delete(f"/v1/admin/users/{u['id']}")
    assert r.status_code == 204
    assert await get_user(db_conn, u["id"]) is None


async def test_patch_cannot_disable_last_active_admin(db_conn):
    """PATCH parity with delete_user's last_active_admin rail: disabling the
    sole remaining active admin must 409, not lock everyone out."""
    from server.auth.users import get_user

    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{admin['id']}", json={"status": "disabled"}
        )
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "last_active_admin"
    assert (await get_user(db_conn, admin["id"]))["status"] == "active"


async def test_patch_cannot_strip_admin_from_last_admin(db_conn):
    """Same rail, capabilities branch: dropping "admin" from the sole active
    admin's capability set must 409 before any KC reconcile happens."""
    from server.auth.users import get_user

    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{admin['id']}", json={"capabilities": ["reader"]}
        )
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "last_active_admin"
    assert "admin" in (await get_user(db_conn, admin["id"]))["capabilities"]


async def test_patch_can_demote_admin_when_another_exists(db_conn):
    """Positive control: the guard only fires when the target IS the last
    active admin — with a second active admin present, stripping "admin"
    from the first is allowed."""
    from server.auth.users import enroll_linked_user, get_user

    admin, p = await _admin(db_conn)
    await enroll_linked_user(
        db_conn, iss="i", sub="second-admin", email="second@x.io",
        capabilities=["admin", "reader"], status="active",
    )
    # No KC dependency override here (same as e.g. test_admin_lists_and_activates):
    # in the test environment get_kc_admin() resolves to None (no service
    # account configured), so the PATCH takes the app-only path and the KC
    # reconcile — irrelevant to this guard — never runs.
    async with _client(_app(db_conn, p)) as c:
        r = await c.patch(
            f"/v1/admin/users/{admin['id']}", json={"capabilities": ["reader"]}
        )
    assert r.status_code == 200
    assert "admin" not in (await get_user(db_conn, admin["id"]))["capabilities"]


async def test_guard_counts_by_capability_not_role_column(db_conn):
    """The last-active-admin guard must key off the `admin` CAPABILITY, not
    the legacy `role` column. `role` is a best-effort mirror of capabilities
    (kept in sync by set_capabilities/enroll_linked_user) but a vestigial
    write path — or a raw UPDATE, as simulated here — can desync it: a row
    with role='admin' but no 'admin' in capabilities. Real admin access is
    gated on the capability alone (deps._principal), so a guard that counted
    by the column would be fooled by such a row into thinking a second admin
    exists, and let the sole real admin be demoted — locking everyone out."""
    from server.auth.users import enroll_linked_user, get_user

    admin, p = await _admin(db_conn)
    # A normal member: no admin capability, role naturally 'member'.
    m = await enroll_linked_user(
        db_conn, iss="i", sub="s-desync", email="m@x.io",
        capabilities=["reader"], status="active",
    )
    assert m["role"] == "member" and "admin" not in m["capabilities"]
    # Simulate the desync this guard exists to defeat: role column flipped to
    # 'admin' directly (bypassing set_capabilities), capabilities untouched.
    await db_conn.execute("UPDATE users SET role='admin' WHERE id=%s", (m["id"],))
    desynced = await get_user(db_conn, m["id"])
    assert desynced["role"] == "admin" and "admin" not in desynced["capabilities"]

    # The seed admin is the only REAL (capability) admin. Stripping its
    # admin capability must still 409 — M's desynced role column must not
    # be counted as "another active admin".
    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{admin['id']}", json={"capabilities": ["reader", "chat"]}
        )
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "last_active_admin"
    assert "admin" in (await get_user(db_conn, admin["id"]))["capabilities"]


async def test_capability_revoke_hard_fails_on_kc_error(db_conn):
    """A KC revoke failure must hard-fail (502) and leave the DB unchanged —
    a swallowed revoke would be re-granted from the token at next login
    (fail-open), unlike the intentionally best-effort status/delete paths."""
    from server.auth.users import enroll_linked_user, get_user
    _, p = await _admin(db_conn)
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-r", email="r@x.io",
                                 capabilities=["reader", "chat"], status="active")

    class _KC(_FakeKC):
        async def remove_realm_roles(self, sub, names):
            raise Exception("kc down")

    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.patch(f"/v1/admin/users/{u['id']}", json={"capabilities": ["reader"]})
    assert r.status_code == 502
    assert set((await get_user(db_conn, u["id"]))["capabilities"]) == {"reader", "chat"}


# ---------- GET /v1/admin/models: the catalog the Models section chooses from ----------

class _CatalogProvider:
    def __init__(self, name, kind, url, fails=False):
        from server.llm.providers.base import ProviderConfig
        self.config = ProviderConfig(name=name, kind=kind, url=url)
        self.fails = fails


class _CatalogRouter:
    """Stands in for the real router: `models` is (provider, name, context_window) and an unreachable provider
    lands in the failed list, as `Router.list_models` reports it."""
    def __init__(self, providers, models):
        self.providers = {p.config.name: p for p in providers}
        self._models = models

    async def list_models(self):
        from server.llm.router import ModelInfo
        from server.llm.types import Capabilities
        listed = [ModelInfo(f"{prov}:{name}", prov, self.providers[prov].config.kind, name,
                            Capabilities(context_window=window))
                  for prov, name, window in self._models if not self.providers[prov].fails]
        return listed, [n for n, p in self.providers.items() if p.fails]


def _catalog_app(principal):
    app = FastAPI()      # no get_conn override: the route must not need a database
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    return app


_ADMIN = deps.Principal(user_id="u1", email="a@x.io", role="admin")


async def test_admin_models_catalog_shape(monkeypatch):
    from server.services import model_router
    local = _CatalogProvider("ollama", "ollama", "http://ollama:11434")
    cloud = _CatalogProvider("openrouter", "openai", "https://openrouter.example.com/api/v1")
    down = _CatalogProvider("lab", "openai", "http://[::1", fails=True)
    router = _CatalogRouter([local, cloud, down], [
        ("ollama", "gemma4:e4b", 131072),
        ("ollama", "gpt-oss:120b-cloud", None),
        ("ollama", model_router.get_config().embed_model, None),   # the embedding model is not a chat model
        ("openrouter", "some/model", 8192),
        ("lab", "hidden", 4096),                                    # a down provider contributes nothing
    ])
    monkeypatch.setattr(admin_router, "get_router", lambda: router)
    monkeypatch.setenv("INFERENCE_NUM_CTX_MAX", "24576")
    async with _client(_catalog_app(_ADMIN)) as client:
        r = await client.get("/v1/admin/models")
    assert r.status_code == 200
    body = r.json()
    assert body["providers"] == [
        {"name": "ollama", "kind": "ollama", "host": "ollama:11434", "detected": "local", "reachable": True},
        {"name": "openrouter", "kind": "openai", "host": "openrouter.example.com", "detected": "cloud",
         "reachable": True},
        {"name": "lab", "kind": "openai", "host": deployment_info.NOT_A_URL, "detected": "cloud",
         "reachable": False},
    ]
    assert body["models"] == [
        {"id": "ollama:gemma4:e4b", "provider": "ollama", "kind": "ollama", "name": "gemma4:e4b",
         "reported_window": 131072, "cloud_name": False},
        {"id": "ollama:gpt-oss:120b-cloud", "provider": "ollama", "kind": "ollama", "name": "gpt-oss:120b-cloud",
         "reported_window": None, "cloud_name": True},
        {"id": "openrouter:some/model", "provider": "openrouter", "kind": "openai", "name": "some/model",
         "reported_window": 8192, "cloud_name": False},
    ]
    assert body["limits"] == {"num_ctx_max": 24576}


async def test_admin_models_lists_models_the_console_left_out(db_conn, monkeypatch):
    from server.services import console_settings, model_settings
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    p = _CatalogProvider("ollama", "ollama", "http://ollama:11434")
    router = _CatalogRouter([p], [("ollama", "a", None), ("ollama", "b", None)])
    monkeypatch.setattr(admin_router, "get_router", lambda: router)
    await console_settings.save(db_conn, model_settings.SECTION, {"chat_models": ["ollama:a"]},
                                updated_by=str(admin["id"]))      # the console narrows the chat models to a
    async with _client(_catalog_app(_ADMIN)) as client:
        names = [m["name"] for m in (await client.get("/v1/admin/models")).json()["models"]]
    assert names == ["a", "b"]    # the admin chooses from everything the env allows


async def test_admin_models_needs_admin(monkeypatch):
    monkeypatch.setattr(admin_router, "get_router", lambda: _CatalogRouter([], []))
    member = deps.Principal(user_id="u2", email="b@x.io", role="member")
    async with _client(_catalog_app(member)) as client:
        assert (await client.get("/v1/admin/models")).status_code == 403


# --- People search / filter / paging (GET /v1/admin/users with parameters) ---


async def _people(db_conn):
    """The admin plus four people to search among; returns (principal, {key: row})."""
    from server.auth import users as users_mod

    admin, p = await _admin(db_conn)
    rows = {}
    for key, kw in {
        "ada": dict(email="ada@example.com", display_name="Ada L.", username="countess",
                    first_name="Ada", last_name="Lovelace", status="active",
                    capabilities=["reader", "chat"]),
        "bob": dict(email="bob@example.com", username="builder", first_name="Bob",
                    last_name="Smith", status="pending", capabilities=["reader"]),
        "cy": dict(email="cy@example.com", display_name="Cy Twombly", status="disabled",
                   capabilities=["docling"]),
        "pct": dict(email="100%_sure@example.com", status="active"),
    }.items():
        rows[key] = await users_mod.enroll_user(db_conn, **kw)
    return p, rows


async def test_admin_users_search_matches_name_email_username_and_full_name(db_conn):
    p, rows = await _people(db_conn)
    async with _client(_app(db_conn, p)) as c:
        async def ids(q):
            r = await c.get("/v1/admin/users", params={"q": q})
            assert r.status_code == 200
            return {u["id"] for u in r.json()}

        ada = {rows["ada"]["id"]}
        assert await ids("ADA@exa") == ada            # email, case-insensitive substring
        assert await ids("countess") == ada           # username
        assert await ids("ada l.") == ada             # display_name
        assert await ids("lovel") == ada              # last_name
        assert await ids("Ada Lovelace") == ada       # first_name || ' ' || last_name
        assert await ids("a lov") == ada              # spans the join
        assert await ids("twombly") == {rows["cy"]["id"]}
        assert await ids("nobody-matches-this") == set()
        r = await c.get("/v1/admin/users", params={"q": "bob"})
        assert set(r.json()[0]) >= {"id", "email", "capabilities", "created_at", "project_limit"}
        assert r.headers["X-Total-Count"] == "1"


async def test_admin_users_search_escapes_like_wildcards(db_conn):
    p, rows = await _people(db_conn)
    async with _client(_app(db_conn, p)) as c:
        for q, want in (("%", "pct"), ("_", "pct"), ("0%_s", "pct")):
            r = await c.get("/v1/admin/users", params={"q": q})
            assert [u["id"] for u in r.json()] == [rows[want]["id"]], q
        r = await c.get("/v1/admin/users", params={"q": "\\"})
        assert r.status_code == 200 and r.json() == []
        assert r.headers["X-Total-Count"] == "0"


async def test_admin_users_filters_status_and_capability(db_conn):
    p, rows = await _people(db_conn)
    me = p.user_id   # the bootstrapped admin: active, has reader, chat and admin
    async with _client(_app(db_conn, p)) as c:
        async def ids(**params):
            r = await c.get("/v1/admin/users", params=params)
            assert r.status_code == 200, r.text
            return {u["id"] for u in r.json()}

        assert await ids(status="pending") >= {rows["bob"]["id"]}
        assert rows["ada"]["id"] not in await ids(status="pending")
        assert await ids(status="disabled") == {rows["cy"]["id"]}
        assert await ids(capability="docling") == {rows["cy"]["id"]}
        assert await ids(capability="reader") == {me, rows["ada"]["id"], rows["bob"]["id"]}
        assert await ids(capability="admin") == {me}
        # filters combine with each other and with q
        assert await ids(capability="reader", status="active") == {me, rows["ada"]["id"]}
        assert await ids(capability="reader", q="smith") == {rows["bob"]["id"]}
        assert await ids(capability="docling", status="active") == set()


async def test_admin_users_pages_with_total_header(db_conn):
    p, rows = await _people(db_conn)
    async with _client(_app(db_conn, p)) as c:
        everyone = (await c.get("/v1/admin/users")).json()   # admin + 4
        assert len(everyone) == 5
        seen = []
        for offset in (0, 2, 4):
            r = await c.get("/v1/admin/users", params={"limit": 2, "offset": offset})
            assert r.status_code == 200
            assert r.headers["X-Total-Count"] == "5"
            seen += [u["id"] for u in r.json()]
        assert len(seen) == 5 and set(seen) == {u["id"] for u in everyone}
        # a bare limit works too; the default page size is 50, so all 5 fit
        r = await c.get("/v1/admin/users", params={"offset": 1})
        assert len(r.json()) == 4 and r.headers["X-Total-Count"] == "5"
        # past the end: an empty page, the total still honest
        r = await c.get("/v1/admin/users", params={"limit": 2, "offset": 50})
        assert r.json() == [] and r.headers["X-Total-Count"] == "5"


async def test_admin_users_without_params_is_unchanged(db_conn):
    p, rows = await _people(db_conn)
    async with _client(_app(db_conn, p)) as c:
        r = await c.get("/v1/admin/users")
        assert r.status_code == 200 and isinstance(r.json(), list)
        assert len(r.json()) == 5                      # every user, not a 50-row page
        assert "x-total-count" not in r.headers


async def test_admin_users_rejects_bad_params(db_conn):
    p, _ = await _people(db_conn)
    async with _client(_app(db_conn, p)) as c:
        for params in (
            {"status": "bogus"}, {"capability": "root"}, {"limit": 0}, {"limit": 201},
            {"limit": "x"}, {"offset": -1}, {"q": "x" * 201},
        ):
            r = await c.get("/v1/admin/users", params=params)
            assert r.status_code == 422, params
        assert (await c.get("/v1/admin/users", params={"q": "x" * 200})).status_code == 200
        assert (await c.get("/v1/admin/users", params={"limit": 200})).status_code == 200


async def test_admin_users_search_requires_admin(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as c:
        assert (await c.get("/v1/admin/users", params={"q": "a"})).status_code == 403
