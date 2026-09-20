import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router


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
    await ib.record_usage(db_conn, admin["id"], 10, 5)
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
    async def create_user(self, *, email, display_name=None, email_verified=False):
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
