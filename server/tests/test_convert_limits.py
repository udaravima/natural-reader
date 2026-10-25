"""Admin console v2, Task 4: POST /v1/docs/{id}/convert holds a request to the admin's Docling limits, and a
refused one never starts a conversion. The harness is test_content_ops.py's."""
from __future__ import annotations

import pytest
from fastapi import FastAPI

from server.auth import deps, scopes as token_scopes
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import admin as admin_router, settings as settings_router
from server.services import console_settings, docling_convert, docling_limits
from server.tests import seed
from server.tests.docs_harness import build_docs_app
from server.tests.pdfgen import make_pdf
from server.tests.test_chat_options import _client

HEX = "a" * 64
ROLE_MESSAGE = "Only people your admin has given the Docling role can convert documents on this server."


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


async def _fake_convert(*_args, **_kwargs):
    return [(1, "# P1")]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DOCLING_CONVERT_BY", "DOCLING_PRESETS", "DOCLING_ALLOW_OCR", "DOCLING_ALLOW_PICTURE_DESCRIPTION",
                "DOCLING_MAX_PAGES"):
        monkeypatch.delenv(var, raising=False)
    console_settings._warned.clear()


@pytest.fixture
def harness(db_conn, monkeypatch, store):
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)
    monkeypatch.setattr(docling_convert, "convert_pdf_to_markdown_pages", _fake_convert)
    return build_docs_app(db_conn, monkeypatch, storage_dir=store)


async def _person(db_conn, sub, role, capabilities):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role=role, capabilities=frozenset(capabilities))


async def _member(db_conn, sub, capabilities=("reader",)):
    return await _person(db_conn, sub, "member", capabilities)


async def _admin(db_conn, sub, capabilities=("reader",)):
    return await _person(db_conn, sub, "admin", capabilities)


def _write(store, doc_id, ext, data):
    path = store / f"{doc_id}.{ext}"
    path.write_bytes(data)
    return path


async def _doc(db_conn, doc_id):
    cur = await db_conn.execute(
        "SELECT state, conversion_state, extracted_by FROM documents WHERE doc_id = %s", (doc_id,))
    row = await cur.fetchone()
    return dict(zip(("state", "conversion_state", "extracted_by"), row)) if row else None


async def _save_documents(db_conn, changes):
    cur = await db_conn.execute("SELECT id FROM users LIMIT 1")
    by = (await cur.fetchone())[0]
    await console_settings.save(db_conn, docling_limits.SECTION, changes, updated_by=str(by))


async def _seeded(db_conn, store, owner, pages=("hi",), **kw):
    path = _write(store, HEX, "pdf", make_pdf(list(pages)))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path, **kw)


async def test_an_unknown_page_count_is_counted_from_the_file(db_conn, harness, store):   # Review Focus 4
    _, as_user = harness
    owner = await _member(db_conn, "pl-owner")
    await _seeded(db_conn, store, owner, pages=["a", "b", "c"])   # page_count NULL
    await _save_documents(db_conn, {"max_pages": 2})
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})
        assert r.status_code == 403 and r.json()["detail"]["option"] == "max_pages"
        assert (await _doc(db_conn, HEX))["conversion_state"] != "converting"
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={"page_range": [1, 2]})).status_code == 202


async def test_a_short_pdf_under_the_limit_is_not_refused(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "pl-short")
    await _seeded(db_conn, store, owner, pages=["a", "b", "c"])
    await _save_documents(db_conn, {"max_pages": 3})
    async with as_user(owner) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202


async def test_a_recorded_page_count_is_used_without_reading_the_file(db_conn, harness, store, monkeypatch):
    _, as_user = harness
    owner = await _member(db_conn, "pl-known")
    await _seeded(db_conn, store, owner, pages=["a"])
    await db_conn.execute("UPDATE documents SET page_count = 600 WHERE doc_id = %s", (HEX,))
    await _save_documents(db_conn, {"max_pages": 100})
    from server.services import extract

    def boom(_path):
        raise AssertionError("the file was opened though page_count is known")
    monkeypatch.setattr(extract, "pdf_page_count", boom)
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})
    assert r.status_code == 403 and r.json()["detail"]["option"] == "max_pages"


async def test_the_file_is_not_opened_when_there_is_no_page_limit(db_conn, harness, store, monkeypatch):
    _, as_user = harness
    owner = await _member(db_conn, "pl-nolimit")
    await _seeded(db_conn, store, owner)
    from server.services import extract

    def boom(_path):
        raise AssertionError("counted pages with no limit set")
    monkeypatch.setattr(extract, "pdf_page_count", boom)
    async with as_user(owner) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202


async def test_a_file_that_cannot_be_counted_is_left_to_docling(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "pl-broken")
    path = _write(store, HEX, "pdf", b"not a pdf")
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path)
    await _save_documents(db_conn, {"max_pages": 2})
    async with as_user(owner) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202


async def test_role_mode_refuses_a_reader_without_the_role_and_lets_role_holders_and_admins_convert(
        db_conn, harness, store):
    _, as_user = harness
    await _save_documents(db_conn, {"convert_by": "role"})
    plain = await _member(db_conn, "rm-plain")
    await _seeded(db_conn, store, plain)
    async with as_user(plain) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})
    assert r.status_code == 403
    assert r.json()["detail"]["option"] == "convert_by" and r.json()["detail"]["message"] == ROLE_MESSAGE
    assert r.json()["detail"]["error"] == "convert_not_allowed"
    assert (await _doc(db_conn, HEX))["conversion_state"] != "converting"

    holder = await _member(db_conn, "rm-holder", ("reader", "docling"))
    await db_conn.execute("DELETE FROM library_entries WHERE doc_id = %s", (HEX,))
    await db_conn.execute("INSERT INTO library_entries (user_id, doc_id, added_via, verified) "
                          "VALUES (%s, %s, 'upload', true)", (holder.user_id, HEX))
    async with as_user(holder) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202

    admin = await _admin(db_conn, "rm-admin")
    await seed.share_doc(db_conn, HEX, holder.user_id, admin.user_id)   # an admin may convert a shared document
    async with as_user(admin) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202


async def test_ocr_and_describe_refusals_name_the_option(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "od-owner")
    await _seeded(db_conn, store, owner)
    await _save_documents(db_conn, {"ocr": False, "picture_description": False})
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={"ocr": True})
        assert r.status_code == 403 and r.json()["detail"]["option"] == "ocr"
        r = await client.post(f"/v1/docs/{HEX}/convert", json={"images": "describe"})
        assert r.status_code == 403 and r.json()["detail"]["option"] == "picture_description"
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={"images": "embed"})).status_code == 202


async def test_accurate_is_refused_when_ocr_is_off(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "ac-owner")
    await _seeded(db_conn, store, owner)
    await _save_documents(db_conn, {"ocr": False})
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={"preset": "accurate"})
        assert r.status_code == 403 and r.json()["detail"]["option"] == "ocr"
        assert r.json()["detail"]["message"] == "Your admin has turned off OCR, which the Accurate preset uses."
        assert (await _doc(db_conn, HEX))["conversion_state"] != "converting"
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={"preset": "standard"})).status_code == 202


async def test_a_documents_scoped_token_with_the_docling_role_converts_in_role_mode(db_conn, harness, store):
    _, as_user = harness
    await _save_documents(db_conn, {"convert_by": "role"})
    u = await resolve_or_provision_user(db_conn, iss="i", sub="tok-owner", email="tok@x.io")
    await set_status(db_conn, u["id"], "active")
    account = frozenset({"reader", "docling"})
    # what deps._principal builds for a token scoped to `documents`
    scoped = deps.Principal(user_id=u["id"], email=u["email"], role="member",
                            capabilities=token_scopes.narrow(account, frozenset({"documents"})),
                            scopes=frozenset({"documents"}), account_capabilities=account)
    await _seeded(db_conn, store, scoped)
    async with as_user(scoped) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202


async def test_an_env_bound_refuses_what_the_console_allowed(db_conn, harness, store, monkeypatch):   # Review Focus 5
    _, as_user = harness
    monkeypatch.setenv("DOCLING_PRESETS", "fast,standard")
    owner = await _member(db_conn, "env-owner")
    await _seeded(db_conn, store, owner)
    await _save_documents(db_conn, {"presets": ["standard"]})
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={"preset": "accurate"})
        assert r.status_code == 403 and r.json()["detail"]["option"] == "preset"
        assert r.json()["detail"]["message"] == "Your admin has turned off the Accurate preset."
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={"preset": "standard"})).status_code == 202


@pytest.mark.parametrize("page_range", [[3, 1], [0, 2], [1], [1, 2, 3], [-1, 4]])
async def test_a_bad_page_range_is_a_422(db_conn, harness, store, page_range):
    _, as_user = harness
    owner = await _member(db_conn, "pr-owner")
    await _seeded(db_conn, store, owner)
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={"page_range": page_range})
    assert r.status_code == 422
    assert (await _doc(db_conn, HEX))["conversion_state"] != "converting"


# ---------- the signed-in user's view, and the console's section ----------

def _settings_app(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.dependency_overrides[deps.require_admin] = lambda: principal
    app.include_router(settings_router.router)
    app.include_router(admin_router.router)
    return app


async def test_settings_docling_view(db_conn, monkeypatch):
    await _save_documents(db_conn, {"convert_by": "role", "presets": ["fast", "standard"], "max_pages": 20})
    plain = await _member(db_conn, "v-plain")
    holder = await _member(db_conn, "v-holder", ("reader", "docling"))
    admin = await _admin(db_conn, "v-admin")
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)
    async with _client(_settings_app(db_conn, plain)) as c:
        body = (await c.get("/v1/settings/docling")).json()
    assert body == {"enabled": True, "may_convert": False, "presets": ["fast", "standard"], "ocr": True,
                    "picture_description": True, "max_pages": 20}
    async with _client(_settings_app(db_conn, holder)) as c:
        assert (await c.get("/v1/settings/docling")).json()["may_convert"] is True
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: False)
    async with _client(_settings_app(db_conn, admin)) as c:
        body = (await c.get("/v1/settings/docling")).json()
    assert body["may_convert"] is True and body["enabled"] is False


async def test_settings_docling_view_needs_a_signed_in_user(db_conn):
    app = FastAPI()

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(settings_router.router)
    async with _client(app) as c:
        assert (await c.get("/v1/settings/docling")).status_code in (401, 403)


async def test_documents_section_is_served_by_the_admin_routes(db_conn):
    admin = await _admin(db_conn, "s-admin")
    async with _client(_settings_app(db_conn, admin)) as c:
        body = (await c.get("/v1/admin/settings/documents")).json()
        assert set(body["settings"]) == {"convert_by", "presets", "ocr", "picture_description", "max_pages"}
        assert body["settings"]["convert_by"]["env"] == "DOCLING_CONVERT_BY"
        assert "docling" in {row["id"] for row in body["info"]}
        r = await c.put("/v1/admin/settings/documents", json={"changes": {"presets": []}})
        assert r.status_code == 422 and r.json()["detail"]["message"] == "Keep at least one preset."
