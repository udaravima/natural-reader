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
