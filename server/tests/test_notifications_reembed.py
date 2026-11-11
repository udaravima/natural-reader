"""Re-embed notices (spec 2026-11-06): admins hear once when a re-embed becomes paused (not on each retry), when it
switches, and how many passages the run shortened; filling again, cancelling or a new start resolves the pause."""
import pytest

from server.services import embedding_migration as mig, embedding_state, embeddings, notifications
from server.services.embeddings import EmbeddingError
from server.tests.test_embedding_migration import AXIS, GEMMA, SEED_ADMIN, _axis, _fill, lib  # noqa: F401 (fixture)


@pytest.fixture
async def admins(db_conn):
    return set(await notifications.admin_ids(db_conn))


async def _notices(conn, kind=None):
    cur = await conn.execute(
        "SELECT user_id::text, kind, level, title, body, key, repeats, target, resolved_at FROM notifications "
        "WHERE (%s::text IS NULL OR kind = %s) ORDER BY id", (kind, kind))
    return await cur.fetchall()


def _failing(monkeypatch, message="ollama: embeddinggemma-2:740m: connection refused"):
    async def boom(e, texts):
        raise EmbeddingError(message)
    monkeypatch.setattr(mig, "embed_texts", boom)


def _working(monkeypatch, *, shorten=0):
    async def ok(e, texts):
        for _ in range(shorten):
            embeddings.note_shortened()
        return [_axis(AXIS[e.model]) for _ in texts]
    monkeypatch.setattr(mig, "embed_texts", ok)


async def test_a_pause_sends_one_notice_to_every_admin(db_conn, lib, admins, monkeypatch):
    assert admins   # the seeded admin, at least
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _failing(monkeypatch, "x" * 300)
    assert await mig.fill_step("w") == "error"
    rows = await _notices(db_conn)
    assert {r[0] for r in rows} == admins and len(rows) == len(admins)
    _, kind, level, title, body, key, repeats, target, resolved = rows[0]
    assert (kind, level, key, repeats, target, resolved) == (
        "reembed.paused", "action", "reembed", 1, {"admin": "models"}, None)
    assert title == f"Re-embedding paused: {GEMMA.model} stopped answering."
    assert body == "x" * 200


async def test_a_pause_that_repeats_is_one_notice(db_conn, lib, admins, monkeypatch):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _failing(monkeypatch)
    for _ in range(5):
        assert await mig.fill_step("w") == "error"
    rows = await _notices(db_conn)
    assert len(rows) == len(admins) and {r[6] for r in rows} == {1}


async def test_a_second_worker_taking_over_a_paused_re_embed_sends_no_second_notice(db_conn, lib, admins,
                                                                                      monkeypatch):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _failing(monkeypatch)
    assert await mig.fill_step("a") == "error"
    assert await mig.fill_step("b") == "claimed-elsewhere"       # "a" holds the claim, so "b" can't fail with it
    state = await embedding_state.read_state(db_conn)
    await embedding_state.write_state(db_conn, {**state, "claimed_at": "2000-01-01T00:00:00+00:00"})   # "a" died
    assert await mig.fill_step("b") == "error"
    assert len(await _notices(db_conn)) == len(admins)


async def test_filling_again_resolves_the_pause(db_conn, lib, monkeypatch):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _failing(monkeypatch)
    await mig.fill_step("w")
    _working(monkeypatch)
    assert await mig.fill_step("w") == "filled"
    assert all(r[8] is not None for r in await _notices(db_conn, "reembed.paused"))


async def test_cancel_resolves_the_pause(db_conn, lib, monkeypatch):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _failing(monkeypatch)
    await mig.fill_step("w")
    await mig.cancel(db_conn, by=SEED_ADMIN)
    assert all(r[8] is not None for r in await _notices(db_conn, "reembed.paused"))


async def test_switch_sends_done_and_the_runs_shortened_count(db_conn, lib, admins, monkeypatch):
    monkeypatch.setattr(mig, "FILL_BATCH", 2)                    # three fills of the six passages
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _working(monkeypatch, shorten=4)
    assert await _fill() == "ready"
    assert await mig.switch(db_conn)
    done = await _notices(db_conn, "reembed.done")
    assert len(done) == len(admins)
    assert done[0][2:4] == ("info", f"The library now uses {GEMMA.model}.") and done[0][7] == {"admin": "models"}
    short = await _notices(db_conn, "embed.shortened")
    assert len(short) == len(admins)
    assert short[0][3] == f"{GEMMA.model} refused 12 passages during the re-embed; they were shortened to fit."
    assert (await embedding_state.read_state(db_conn))["shortened"] == 0


async def test_a_switch_with_nothing_shortened_sends_only_done(db_conn, lib, monkeypatch):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    _working(monkeypatch)
    await _fill()
    assert await mig.switch(db_conn)
    assert await _notices(db_conn, "embed.shortened") == []
    assert await _notices(db_conn, "reembed.done") != []


async def test_a_refused_new_model_resolves_the_pause_of_the_re_embed_it_stops(db_conn, lib, monkeypatch):
    # The env names another model while the re-embed is paused, and that model is refused: the paused re-embed is
    # dropped, so its notice no longer needs anyone.
    from server.services.embeddings import ProbeResult
    from server.tests.test_embedding_migration import MXBAI, _probes
    _seen, probe = _probes({MXBAI.model: ProbeResult(False, 1024, None, "mxbai-embed-large gives 1024 numbers")})
    monkeypatch.setattr(mig, "probe", probe)
    monkeypatch.setenv("EMBEDDING_MODEL", GEMMA.model)
    assert await mig.tick("w") == "started"
    _failing(monkeypatch)
    assert await mig.fill_step("w") == "error"
    assert [r[8] for r in await _notices(db_conn, "reembed.paused")] != []
    monkeypatch.setenv("EMBEDDING_MODEL", MXBAI.model)
    assert await mig.tick("w") == "refused"
    assert all(r[8] is not None for r in await _notices(db_conn, "reembed.paused"))
