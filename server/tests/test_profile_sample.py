"""Try it: the unsaved profile answers one question, with the model the admin picks, no tools, no documents, and
nothing stored (character profiles spec, "Hear a sample reply")."""
import logging

from server.chat.profile_text import BACKSTORY_LABEL
from server.services import helper, profile_library
from server.tests.test_profile_draft import OFF, _client, _principal, helper_as  # noqa: F401  (fixture)

BODY = {"name": "Grandpa Siri", "backstory": "You are Siri. SECRET-PAST.", "text": "- Stay in character.",
        "question": "Introduce yourself.", "model": "ollama:big"}


def _config_for(monkeypatch, asked):
    async def config_for(conn, model_id, env=None):
        asked.append(model_id)
        return helper.HelperConfig(model=model_id, timeout_s=5.0, input_chars=6000, num_ctx=None, local=True)
    monkeypatch.setattr(helper, "config_for", config_for)


async def test_the_profile_answers_as_a_chat_without_documents_would_and_nothing_is_kept(db_conn, helper_as,
                                                                                         monkeypatch, caplog):
    admin = await _principal(db_conn, "a1", "admin")
    await profile_library.save_house_rules(db_conn, "Be kind.", updated_by=None)
    fake = helper_as(OFF, "Ah, come and sit, child. I am Siri.")
    asked = []
    _config_for(monkeypatch, asked)
    cur = await db_conn.execute("SELECT count(*) FROM chat_sessions")
    before = (await cur.fetchone())[0]
    with caplog.at_level(logging.DEBUG):
        async with _client(db_conn, admin) as c:
            r = await c.post("/v1/admin/assistant/sample", json=BODY)
    assert r.status_code == 200, r.text
    assert r.json() == {"reply": "Ah, come and sit, child. I am Siri."}
    [(model, messages, settings)] = fake.calls
    assert asked == ["ollama:big"] and model == "ollama:big" and settings.temperature == 0.7
    system = messages[0].content
    assert system.startswith(f"Be kind.\n\n{BACKSTORY_LABEL}\nYou are Siri. SECRET-PAST.\n\n- Stay in character."
                             "\n\nYou are working in Natural Reader")
    assert "How to handle each message:" in system and "search_documents" not in system
    assert [m.role for m in messages] == ["system", "user"] and messages[1].content == "Introduce yourself."
    cur = await db_conn.execute("SELECT count(*) FROM chat_sessions")
    assert (await cur.fetchone())[0] == before
    assert "SECRET-PAST" not in caplog.text and "Siri" not in caplog.text


async def test_the_route_says_why_it_cannot(db_conn, helper_as, monkeypatch):
    from server.llm.router import UnknownModel
    admin = await _principal(db_conn, "a1", "admin")
    helper_as(OFF, "   ")
    _config_for(monkeypatch, [])
    async with _client(db_conn, admin) as c:
        r = await c.post("/v1/admin/assistant/sample", json=BODY)
        assert (r.status_code, r.json()["detail"]["error"]) == (502, "sample_failed")
        assert (await c.post("/v1/admin/assistant/sample", json={**BODY, "question": ""})).status_code == 422
        assert (await c.post("/v1/admin/assistant/sample", json={**BODY, "model": ""})).status_code == 422

        async def nowhere(conn, model_id, env=None):
            raise UnknownModel(model_id)
        monkeypatch.setattr(helper, "config_for", nowhere)
        r = await c.post("/v1/admin/assistant/sample", json=BODY)
        assert (r.status_code, r.json()["detail"]["error"]) == (422, "unknown_model")
