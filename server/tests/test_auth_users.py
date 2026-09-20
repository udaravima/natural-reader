import pytest

from server.auth.sessions import create_session, resolve_session
from server.auth.users import resolve_or_provision_user, get_user, set_status

SEED = "00000000-0000-0000-0000-000000000001"


async def test_first_login_links_seed_admin(db_conn):
    # Fresh DB: only the unlinked seed admin exists -> first login becomes admin.
    u = await resolve_or_provision_user(
        db_conn, iss="https://kc/realms/nr", sub="abc", email="me@x.io"
    )
    assert u["id"] == SEED
    assert u["role"] == "admin" and u["status"] == "active"
    assert u["oidc_sub"] == "abc"


async def test_verified_email_links_preseeded_row(db_conn):
    await db_conn.execute("UPDATE users SET email='real@x.io' WHERE id=%s", (SEED,))
    u = await resolve_or_provision_user(
        db_conn, iss="i", sub="s1", email="real@x.io", email_verified=True
    )
    assert u["id"] == SEED and u["oidc_sub"] == "s1"


async def test_unverified_email_cannot_claim_preseeded_row(db_conn):
    # An unverified email matching the pre-seeded admin must be refused, not linked.
    await db_conn.execute("UPDATE users SET email='real@x.io' WHERE id=%s", (SEED,))
    with pytest.raises(ValueError):
        await resolve_or_provision_user(
            db_conn, iss="i", sub="s1", email="real@x.io", email_verified=False
        )
    # Seed stays unlinked.
    assert (await get_user(db_conn, SEED))["oidc_sub"] is None


async def test_disabling_user_revokes_sessions(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")  # admin
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u["id"], "active")
    tok = await create_session(db_conn, u["id"])
    assert await resolve_session(db_conn, tok) is not None
    await set_status(db_conn, u["id"], "disabled")
    assert await resolve_session(db_conn, tok) is None


async def test_second_identity_is_pending_member(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    assert u2["role"] == "member" and u2["status"] == "pending"


async def test_returning_user_matched_by_sub(db_conn):
    a = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    b = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    assert a["id"] == b["id"]


async def test_set_status(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u["id"], "active")
    assert (await get_user(db_conn, u["id"]))["status"] == "active"


async def test_first_login_claims_seed_admin_with_bootstrap_caps(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    assert u["id"] == SEED
    assert set(u["capabilities"]) == {"admin", "reader", "chat"}


async def test_brand_new_user_is_pending_with_no_caps(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")  # seed
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io",
                                        capabilities=[])
    assert m["status"] == "pending" and m["capabilities"] == []


async def test_known_identity_caps_synced_from_token(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")  # seed
    await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io",
                                    capabilities=[])
    synced = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io",
                                             capabilities=["reader"])
    assert set(synced["capabilities"]) == {"reader"} and synced["role"] == "member"
