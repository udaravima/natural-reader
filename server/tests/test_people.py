"""A0 §4: people's names from the login token, one labelling rule, and the
directory lookup (exact / domain / open)."""
import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import people as people_router
from server.services import people


async def _person(conn, sub, email, *, first=None, last=None, username=None, status="active"):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=email, username=username,
                                        first_name=first, last_name=last)
    await set_status(conn, u["id"], status)
    return u["id"]


def _cfg(mode="open", domains=(), show_email=False):
    return people.DirectoryConfig(mode=mode, domains=frozenset(domains), show_email=show_email)


@pytest.mark.asyncio
async def test_login_stores_names_and_a_missing_claim_keeps_them(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="n1", email="n1@x.io",
                                        username="asha", first_name="Asha", last_name="Perera")
    assert (u["username"], u["first_name"], u["last_name"]) == ("asha", "Asha", "Perera")
    again = await resolve_or_provision_user(db_conn, iss="i", sub="n1", email="n1@x.io")
    assert (again["username"], again["first_name"], again["last_name"]) == ("asha", "Asha", "Perera")
    assert again["project_limit"] is None


@pytest.mark.parametrize("fields,show_email,expected", [
    (dict(first_name="Asha", last_name="Perera", display_name="A", username="asha"), False, "Asha Perera"),
    (dict(first_name=None, last_name="Perera", display_name=None, username=None), False, "Perera"),
    (dict(first_name=None, last_name=None, display_name="Asha P", username="asha"), False, "Asha P"),
    (dict(first_name=None, last_name=None, display_name=None, username="asha"), False, "@asha"),
    # An email-shaped username is hidden while emails are hidden.
    (dict(first_name=None, last_name=None, display_name=None, username="asha@x.io"), False, "a•••@x.io"),
    (dict(first_name=None, last_name=None, display_name=None, username="asha@x.io"), True, "@asha@x.io"),
    (dict(first_name=None, last_name=None, display_name=None, username=None), True, "asha@x.io"),
])
def test_person_label(fields, show_email, expected):
    assert people.person_label(email="asha@x.io", show_email=show_email, **fields) == expected


def test_load_directory_config_defaults_and_bad_values(caplog):
    assert people.load_directory_config({}) == _cfg(mode="exact")
    cfg = people.load_directory_config({"USER_DIRECTORY_MODE": "Domain",
                                        "USER_DIRECTORY_DOMAINS": " Example.com, ,corp.example.com",
                                        "USER_DIRECTORY_SHOW_EMAIL": "true"})
    assert cfg == _cfg(mode="domain", domains={"example.com", "corp.example.com"}, show_email=True)
    with caplog.at_level(logging.WARNING):
        bad = people.load_directory_config({"USER_DIRECTORY_MODE": "everyone",
                                            "USER_DIRECTORY_SHOW_EMAIL": "yes"})
    assert bad == _cfg(mode="exact")
    assert "USER_DIRECTORY_MODE" in caplog.text and "USER_DIRECTORY_SHOW_EMAIL" in caplog.text


@pytest.mark.asyncio
async def test_exact_email_finds_active_and_pending_never_disabled_or_self(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "Ann@Partner.org", first="Ann")
    await _person(db_conn, "p", "pen@example.com", status="pending")
    await _person(db_conn, "d", "dis@example.com", status="disabled")
    cfg = _cfg(mode="exact")
    found = await people.lookup(db_conn, cfg, me, "me@example.com", "ann@partner.org")
    assert [(r["name"], r["status"]) for r in found] == [("Ann", "active")]
    assert "email" not in found[0]
    assert len(await people.lookup(db_conn, cfg, me, "me@example.com", "pen@example.com")) == 1
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "dis@example.com") == []
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "me@example.com") == []


@pytest.mark.asyncio
async def test_exact_mode_has_no_type_ahead(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann")
    assert await people.lookup(db_conn, _cfg(mode="exact"), me, "me@example.com", "an") == []


@pytest.mark.asyncio
async def test_type_ahead_needs_two_characters_and_lists_only_active(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann", last="Lee", username="annl")
    await _person(db_conn, "p", "anna@example.com", first="Anna", status="pending")
    cfg = _cfg(mode="open")
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "a") == []
    found = await people.lookup(db_conn, cfg, me, "me@example.com", "an")
    assert [(r["name"], r["username"]) for r in found] == [("Ann Lee", "annl")]
    assert [r["name"] for r in await people.lookup(db_conn, cfg, me, "me@example.com", "le")] == ["Ann Lee"]


@pytest.mark.asyncio
async def test_domain_mode_only_for_listed_domains(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann")
    await _person(db_conn, "b", "anne@other.org", first="Anne")
    gmail = await _person(db_conn, "g", "g@gmail.com")
    cfg = _cfg(mode="domain", domains={"example.com"})
    assert [r["name"] for r in await people.lookup(db_conn, cfg, me, "me@example.com", "an")] == ["Ann"]
    # An unlisted caller domain falls back to exact: no type-ahead at all.
    assert await people.lookup(db_conn, cfg, gmail, "g@gmail.com", "an") == []


@pytest.mark.asyncio
async def test_lookup_escapes_like_wildcards(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann")
    cfg = _cfg(mode="open")
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "%%") == []
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "a_") == []


@pytest.mark.asyncio
async def test_email_shaped_usernames_never_match_while_emails_are_hidden(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "zed@example.com", username="zed@example.com")
    assert await people.lookup(db_conn, _cfg(mode="open"), me, "me@example.com", "ze") == []
    shown = await people.lookup(db_conn, _cfg(mode="open", show_email=True), me, "me@example.com", "ze")
    assert [r["email"] for r in shown] == ["zed@example.com"]


@pytest.mark.asyncio
async def test_lookup_route_needs_reader_and_logs_no_query(db_conn, caplog, monkeypatch):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "secret.person@example.com", first="Secret")
    monkeypatch.setenv("USER_DIRECTORY_MODE", "open")
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(people_router.router)
    for caps, status in ((frozenset(), 403), (frozenset({"reader"}), 200)):
        app.dependency_overrides[deps.get_current_user] = lambda caps=caps: deps.Principal(
            user_id=me, email="me@example.com", role="member", capabilities=caps)
        with caplog.at_level(logging.DEBUG):
            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get("/v1/users/lookup", params={"q": "secret"})
        assert r.status_code == status
    assert [p["name"] for p in r.json()] == ["Secret"]
    # httpx logs the request URL itself; uvicorn's access log is covered by the
    # logging_config.DropQueryString filter. This checks this app's own loggers.
    assert not any("secret" in rec.getMessage().lower()
                   for rec in caplog.records if rec.name.startswith("server"))
