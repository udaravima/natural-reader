"""Placeholders, and which profile a turn uses (profile library spec, "What a turn sends", "Placeholders")."""
from datetime import datetime, timezone

from server.chat import profile_text as pt
from server.chat import prompt
from server.chat.context import today_line, today_text
from server.services import app_identity, assistant_profile
from server.services import profile_library as lib
from server.tests.chat_harness import member

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def test_fill_replaces_the_three_placeholders_and_says_which_it_used():
    got = pt.fill("You are {{profile}} in {{ app }}. It is {{date}}. {{date}}", date="Sunday, 15 November 2026 (UTC)",
                  app="Acme Docs", profile="Tutor")
    assert got.text == ("You are Tutor in Acme Docs. It is Sunday, 15 November 2026 (UTC). "
                        "Sunday, 15 November 2026 (UTC)")
    assert got.used == frozenset({"profile", "app", "date"})
    assert pt.fill("Plain.", date="d", app="a", profile="p") == pt.Filled("Plain.", frozenset())


def test_an_unknown_placeholder_is_left_as_written_and_reported():
    text = "Hello {{dat}} and {{user}}, {{DATE}}, {single}, {{date}}."
    got = pt.fill(text, date="D", app="a", profile="p")
    assert got.text == "Hello {{dat}} and {{user}}, {{DATE}}, {single}, D."
    assert pt.unknown(text) == ["{{dat}}", "{{user}}", "{{DATE}}"]
    assert pt.unknown("{{date}} {{app}} {{profile}}") == []


def test_a_filled_value_is_never_filled_again():
    got = pt.fill("{{profile}}", date="D", app="A", profile="{{date}}")
    assert got.text == "{{date}}" and got.used == frozenset({"profile"})


def test_a_missing_profile_name_reads_as_the_assistant():
    assert pt.fill("I am {{profile}}.", date="D", app="A", profile="").text == "I am the assistant."


def test_today_text_is_the_date_inside_today_line():
    assert today_text(NOW, "UTC") == "Sunday, 15 November 2026 (UTC)"
    assert today_line(NOW, "UTC") == "Today is Sunday, 15 November 2026 (UTC)."


def test_the_apps_line_leaves_out_what_the_profile_placed():
    ident = app_identity.current()
    line = prompt.first_line(True, ident)
    today = today_line(NOW, "UTC")

    def rules(placed):
        return prompt.system_rules(None, [], has_pins=False, profile="P", today=today, placed=frozenset(placed))
    assert rules(()).startswith(f"P\n\n{line} {today}\n\n")
    assert rules({"date"}).startswith(f"P\n\n{line}\n\n") and "Today is" not in rules({"date"})
    assert rules({"app"}).startswith(f"P\n\n{today}\n\n") and line not in rules({"app"})
    both = rules({"app", "date"})
    assert line not in both and "Today is" not in both and both.startswith("P\n\nHow to")
    assert rules({"profile"}) == rules(())


def test_house_rules_alone_do_not_give_the_assistant_an_identity():
    ident = app_identity.current()
    got = prompt.system_rules(None, [], has_pins=False, profile="Be kind.", persona=False)
    assert got.startswith("Be kind.\n\n" + prompt.first_line(False, ident))


async def _chat(conn, owner, sid="s-1", profile=None, name=None):
    await conn.execute("INSERT INTO chat_sessions (id, title, model, user_id, profile_id, profile_name) "
                       "VALUES (%s, 't', 'm', %s, %s, %s)", (sid, owner, profile, name))


async def test_resolve_follows_asked_then_the_chat_then_the_default(db_conn, monkeypatch):
    monkeypatch.delenv("CHAT_ASSISTANT_PROFILE_FILE", raising=False)
    alice = await member(db_conn, "alice")
    await _chat(db_conn, alice.user_id)
    none = await pt.resolve(db_conn, session_id="s-1", asked_id=None)
    assert (none.id, none.name, none.text, none.house_rules, none.combined) == (None, "", "", "", "")

    a = await lib.create_profile(db_conn, name="Plain", description="", text="You are plain.", updated_by=None)
    b = await lib.create_profile(db_conn, name="Tutor", description="", text="You are a tutor.", updated_by=None)
    await lib.save_house_rules(db_conn, "Be kind.", updated_by=None)
    got = await pt.resolve(db_conn, session_id="s-1", asked_id=None)                 # 3: the default
    assert (got.id, got.name, got.combined) == (a.id, "Plain", "Be kind.\n\nYou are plain.")
    got = await pt.resolve(db_conn, session_id="s-1", asked_id=b.id)                 # 1: asked
    assert (got.id, got.name) == (b.id, "Tutor")
    await db_conn.execute("UPDATE chat_sessions SET profile_id = %s, profile_name = 'Tutor'", (b.id,))
    assert (await pt.resolve(db_conn, session_id="s-1", asked_id=None)).id == b.id   # 2: the chat's own
    assert (await pt.resolve(db_conn, session_id="s-1", asked_id="junk")).id == b.id
    assert (await pt.resolve(db_conn, session_id="s-1", asked_id=a.id)).id == a.id   # asked wins over the chat's

    await lib.update_profile(db_conn, b.id, enabled=False, updated_by=None)
    assert (await pt.resolve(db_conn, session_id="s-1", asked_id=None)).id == b.id   # off: the chat keeps it
    await _chat(db_conn, alice.user_id, sid="s-2")
    assert (await pt.resolve(db_conn, session_id="s-2", asked_id=b.id)).id == a.id   # off: can't be newly asked for

    await lib.delete_profile(db_conn, b.id)
    got = await pt.resolve(db_conn, session_id="s-1", asked_id=None)                 # deleted: the default
    assert (got.id, got.name) == (a.id, "Plain")


async def test_the_file_is_the_profile_while_the_library_is_empty(db_conn, monkeypatch, tmp_path):
    f = tmp_path / "p.md"
    f.write_text("You are Ada.")
    monkeypatch.setenv("CHAT_ASSISTANT_PROFILE_FILE", str(f))
    got = await pt.resolve(db_conn, session_id=None, asked_id=None)
    assert (got.id, got.name, got.text, got.source) == (None, "", "You are Ada.", "file")
    await lib.create_profile(db_conn, name="Plain", description="", text="You are plain.", updated_by=None)
    got = await pt.resolve(db_conn, session_id=None, asked_id=None)
    assert (got.text, got.source) == ("You are plain.", "library")


async def test_largest_is_the_longest_text_any_chat_can_be_sent(db_conn, monkeypatch):
    monkeypatch.delenv("CHAT_ASSISTANT_PROFILE_FILE", raising=False)
    assert await pt.largest(db_conn) == ""
    await lib.create_profile(db_conn, name="A", description="", text="short", updated_by=None)
    await lib.create_profile(db_conn, name="B", description="", text="x" * 500, updated_by=None)
    await lib.save_house_rules(db_conn, "Be kind.", updated_by=None)
    assert await pt.largest(db_conn) == "Be kind.\n\n" + "x" * 500
    assert assistant_profile.PROFILE_MAX_CHARS == 8000
