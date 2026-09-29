import logging
from datetime import datetime, timezone

import pytest

from server.chat import context as ctx_mod
from server.chat.config import ChatConfig, load_chat_config
from server.chat.context import TurnInput, build_context, pin_text, prefetch, time_line
from server.chat.tools import search_documents
from server.chat.store import StoredMessage
from server.llm.types import Attachment, Message
from server.services.doc_search import ReadableDoc

NOW = datetime(2026, 9, 26, 19, 24, tzinfo=timezone.utc)
DOC = ReadableDoc("d" * 64, "Thesis.pdf", "indexed")


class _NullPool:
    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    def connection(self):
        return self._Ctx()


@pytest.fixture
def search(monkeypatch):
    """Scripted embedding + search; records calls."""
    state = {"rows": [], "embeds": 0, "raise": None}

    async def fake_embed(text):
        state["embeds"] += 1
        if state["raise"]:
            raise state["raise"]
        return [1.0]

    async def fake_search(conn, doc_id, qvec, k):
        state["k"] = k
        return state["rows"]

    monkeypatch.setattr(ctx_mod, "embed_query", fake_embed)
    monkeypatch.setattr(ctx_mod, "search_chunks", fake_search)
    monkeypatch.setattr(ctx_mod, "get_pool", lambda: _NullPool())
    return state


def _turn(**over):
    base = dict(user_id="u", text="What does chapter 2 say?", attachments=(), doc=None,
                timezone="Asia/Colombo", pins=[], history=[], window=None, now=NOW)
    base.update(over)
    return TurnInput(**base)


def test_config_defaults_and_bad_values(caplog):
    assert load_chat_config({}) == ChatConfig()
    with caplog.at_level(logging.WARNING):
        cfg = load_chat_config({"CHAT_PREFETCH_MIN_SCORE": "2", "CHAT_PREFETCH_K": "x",
                                "CHAT_MAX_TOOL_ROUNDS": "5"})
    assert (cfg.prefetch_min_score, cfg.prefetch_k, cfg.max_tool_rounds) == (0.6, 4, 5)
    assert "CHAT_PREFETCH_MIN_SCORE" in caplog.text and "CHAT_PREFETCH_K" in caplog.text


def test_time_line_uses_the_browser_timezone():
    assert time_line(NOW, "Asia/Colombo") == "Current time: 2026-09-27 00:54 (Asia/Colombo)"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "", None, "../etc", "America"])
def test_time_line_falls_back_to_utc_on_bad_timezone(bad):
    assert time_line(NOW, bad) == "Current time: 2026-09-26 19:24 (UTC)"


def test_a_pin_is_a_fenced_excerpt():
    # v2.3 Task A: fenced as document text; no tool named; no "(page, page 4)".
    assert pin_text([{"fileName": "T.pdf", "kind": "page", "page": 4, "text": "the excerpt"}]) == (
        "The user pinned this excerpt as primary context for their question:\n"
        '<pinned_excerpt source="T.pdf" where="page 4">\nthe excerpt\n</pinned_excerpt>')
    assert pin_text([]) == ""


async def test_prefetch_keeps_only_passages_above_the_threshold(search):
    search["rows"] = [{"page": 3, "score": 0.91, "text": "a"}, {"page": 5, "score": 0.80, "text": "b"},
                      {"page": 8, "score": 0.40, "text": "c"}]
    got = await prefetch(DOC, "q", ChatConfig())
    assert [p["page"] for p in got.passages] == [3, 5] and search["k"] == 4
    assert got.note == {"kind": "prefetch", "docId": DOC.doc_id, "docName": "Thesis.pdf",
                        "count": 2, "topScore": 0.91, "pages": [3, 5]}


async def test_prefetch_miss_disabled_unreadable_and_failure_cost_nothing_visible(search):
    search["rows"] = [{"page": 1, "score": 0.5, "text": "weak"}]
    miss = await prefetch(DOC, "q", ChatConfig())
    assert miss.passages == [] and miss.note is None and miss.top_score == 0.5
    assert (await prefetch(DOC, "q", ChatConfig(prefetch_min_score=1.0))).passages == []
    assert (await prefetch(None, "q", ChatConfig())).passages == []
    assert (await prefetch(ReadableDoc("x", "n", "extracting"), "q", ChatConfig())).passages == []
    assert search["embeds"] == 1            # disabled / no doc / not indexed never embed
    search["raise"] = RuntimeError("embedding service down")
    assert (await prefetch(DOC, "q", ChatConfig())).passages == []


async def test_message_order_pins_history_then_volatile_and_question_in_one_user_message(search, monkeypatch):
    search["rows"] = [{"page": 3, "score": 0.9, "text": "passage text"}]
    history = [StoredMessage("u1", "user", "earlier q", []), StoredMessage("a1", "assistant", "earlier a", [])]
    built = await build_context(_turn(doc=DOC, pins=[{"text": "pinned"}], history=history), ChatConfig())
    assert built.messages[0].role == "system" and "pinned" in built.messages[0].content
    assert built.messages[0].content.startswith("You are the assistant in Natural Reader")
    assert [m.content for m in built.messages[1:3]] == ["earlier q", "earlier a"]
    # Final review I3: the volatile block is no longer a mid-list system
    # message (strict templates raise on it); it leads the new user message.
    last = built.messages[3]
    assert len(built.messages) == 4 and last.role == "user"
    volatile, question = last.content.rsplit("\n\n", 1)
    assert volatile.startswith('<document_passages source="Thesis.pdf">\n[1] (page 3)\npassage text\n</document_passages>')
    assert volatile.endswith("Current time: 2026-09-27 00:54 (Asia/Colombo)")
    assert question == "What does chapter 2 say?"
    assert built.prefetch_hit and built.notes[0]["kind"] == "prefetch"


async def test_a_prefetch_miss_still_tells_the_model_which_document_is_open(search):
    # Walk: with nothing retrieved, the prompt never named the document, so a
    # small model sent "Zephyr station" questions to web_search every time.
    # v2.3: the rules name it (and its search tool, only when offered).
    search["rows"] = [{"page": 1, "score": 0.2, "text": "weak"}]
    built = await build_context(_turn(doc=DOC, tools=(search_documents.TOOL,)), ChatConfig())
    system = built.messages[0].content
    assert 'The user has the document "Thesis.pdf" open in the reader.' in system
    assert "search_documents" in system
    assert not built.prefetch_hit
    assert built.messages[-1].content == ("Current time: 2026-09-27 00:54 (Asia/Colombo)\n\n"
                                          "What does chapter 2 say?")

    no_doc = await build_context(_turn(doc=None), ChatConfig())
    assert [m.role for m in no_doc.messages] == ["system", "user"]
    assert "Thesis.pdf" not in no_doc.messages[0].content
    unindexed = await build_context(_turn(doc=ReadableDoc(DOC.doc_id, "Thesis.pdf", "extracting")), ChatConfig())
    assert "search_documents" not in "".join(m.content for m in unindexed.messages)


def _assert_strict_template_shape(messages, time_text="Current time: 2026-09-27 00:54 (Asia/Colombo)"):
    """What Gemma/Mistral-style chat templates accept: at most one system
    message, only first; then user/assistant strictly alternating, starting
    and ending on user; the last user message carries the volatile block."""
    systems = [i for i, m in enumerate(messages) if m.role == "system"]
    assert systems in ([], [0]), f"system messages at {systems}"
    rest = messages[1:] if systems else messages
    roles = [m.role for m in rest]
    assert roles == ["user", "assistant"] * (len(roles) // 2) + ["user"], roles
    assert time_text in rest[-1].content


async def test_the_prompt_fits_strict_chat_templates(search, monkeypatch):
    """Final review I3: vLLM's Gemma/Mistral templates raise on any system
    message after the first and on two same-role messages in a row."""
    search["rows"] = [{"page": 3, "score": 0.9, "text": "passage text"}]
    img = Attachment("image", "image/png", "QQ==", "a.png")

    async def fake_bytes(wanted):
        return {w: img for w in wanted}

    monkeypatch.setattr(ctx_mod.store, "load_attachment_bytes", fake_bytes)
    meta = {"id": "i", "kind": "image", "name": "a.png", "mimeType": "image/png", "size": 4, "ordinal": 0}
    # Two pins, and a history with two user turns in a row (a reply that
    # failed and was skipped) and a trailing unanswered user turn.
    history = [StoredMessage("u1", "user", "q1", [meta]), StoredMessage("u2", "user", "q2", []),
               StoredMessage("a2", "assistant", "a2", []), StoredMessage("u3", "user", "q3 unanswered", [])]
    now_att = Attachment("image", "image/png", "Qg==", "now.png")
    pins = [{"text": "pin one"}, {"text": "pin two"}]
    built = await build_context(_turn(doc=DOC, pins=pins, history=history, attachments=(now_att,)), ChatConfig())
    m = built.messages
    _assert_strict_template_shape(m)
    # The rules, then both pins, in ONE leading system message.
    assert m[0].content.endswith(pin_text(pins))
    assert m[1] == Message("user", "q1\n\nq2", attachments=(img,))
    assert m[2].content == "a2"
    # The unanswered turn joins the new message, after the volatile block.
    assert m[3].content.startswith('<document_passages source="Thesis.pdf">')
    assert m[3].content.endswith("q3 unanswered\n\nWhat does chapter 2 say?")
    assert m[3].attachments == (now_att,)


async def test_no_window_means_no_trimming(search):
    history = [StoredMessage(f"m{i}", "user" if i % 2 == 0 else "assistant", "x" * 1000, []) for i in range(50)]
    built = await build_context(_turn(history=history, window=None), ChatConfig())
    assert len(built.messages) == 52 and built.notes == []   # rules + 50 history + the new message


async def test_old_attachments_go_before_old_turns_and_only_kept_bytes_load(search, monkeypatch):
    asked = {}

    async def fake_bytes(wanted):
        asked["wanted"] = wanted
        return {w: Attachment("image", "image/png", "QQ==", "img") for w in wanted}

    monkeypatch.setattr(ctx_mod.store, "load_attachment_bytes", fake_bytes)
    img = lambda n: {"id": n, "kind": "image", "name": f"{n}.png", "mimeType": "image/png", "size": 9, "ordinal": 0}
    history = [StoredMessage("u1", "user", "look at this", [img("old")]),
               StoredMessage("a1", "assistant", "a cat", []),
               StoredMessage("u2", "user", "and this", [img("new")]),
               StoredMessage("a2", "assistant", "a dog", [])]
    # window: fixed text is ~40 tokens; each attachment is estimated at 1500.
    # 2048 reserve + ~40 + one attachment (1500) fits in 3700; two don't.
    built = await build_context(_turn(history=history, window=3700), ChatConfig())
    assert asked["wanted"] == [("u2", 0)]
    first = built.messages[1]   # [0] is the system rules
    assert first.attachments == () and first.content == "look at this\n\n[image old.png from earlier; no longer attached]"
    assert len(built.messages[3].attachments) == 1
    assert built.notes == [{"kind": "trimmed", "messages": 0, "attachments": 1}]


async def test_missing_attachment_bytes_get_the_same_marker_as_a_trimmed_one(search, monkeypatch):
    """An attachment can survive trimming (it's in `keep`) and still have no
    row in Postgres — e.g. its bytes were deleted independently of the
    message. That must not silently vanish from the model's view; it gets
    the exact marker a deliberately-trimmed attachment gets, not a duplicate
    string."""
    async def fake_bytes(wanted):
        return {}   # no row for anything asked

    monkeypatch.setattr(ctx_mod.store, "load_attachment_bytes", fake_bytes)
    img = {"id": "gone", "kind": "image", "name": "gone.png", "mimeType": "image/png", "size": 9, "ordinal": 0}
    history = [StoredMessage("u1", "user", "look at this", [img]), StoredMessage("a1", "assistant", "ok", [])]
    built = await build_context(_turn(history=history, window=None), ChatConfig())
    first = built.messages[1]   # [0] is the system rules
    assert first.attachments == ()
    assert first.content == "look at this\n\n[image gone.png from earlier; no longer attached]"


async def test_then_the_oldest_turns_are_dropped(search):
    history = [StoredMessage(f"m{i}", "user" if i % 2 == 0 else "assistant", "x" * 400, []) for i in range(10)]
    # reserve 2048 + question ~18 fixed tokens + 10 x 100 tokens/message.
    # Budget alone (window 2600 -> budget 552) drops 5 of the alternating
    # m0..m9 (user/assistant/user/...), which would leave m5 ('assistant')
    # at the head -- a reply with no question before it. Trimming then drops
    # that one extra turn too so the kept history starts on 'user' again:
    # 6 dropped in total, 4 remain (m6..m9).
    built = await build_context(_turn(history=history, window=2600), ChatConfig())
    kept = [m for m in built.messages if m.content == "x" * 400]
    assert len(kept) == 4
    assert kept[0].role == "user"
    assert built.notes == [{"kind": "trimmed", "messages": 6, "attachments": 0}]


async def test_trimming_never_leaves_a_non_user_message_at_the_head(search):
    # Irregular history (two user turns in a row, e.g. a reply that errored
    # and was excluded by load_turn_context): budget alone drops m0 and m1
    # ('user','user'), which would leave m2 ('assistant') at the head.
    # Trimming must keep dropping until the head is 'user' again.
    history = [StoredMessage("m0", "user", "x" * 400, []),
               StoredMessage("m1", "user", "x" * 400, []),
               StoredMessage("m2", "assistant", "x" * 400, []),
               StoredMessage("m3", "user", "x" * 400, []),
               StoredMessage("m4", "assistant", "x" * 400, [])]
    built = await build_context(_turn(history=history, window=2398), ChatConfig())
    kept = [m for m in built.messages if m.content == "x" * 400]
    assert [m.role for m in kept] == ["user", "assistant"]
    assert built.notes == [{"kind": "trimmed", "messages": 3, "attachments": 0}]


async def test_the_current_message_keeps_its_attachments_even_when_over_budget(search):
    att = Attachment("image", "image/png", "QQ==", "now.png")
    built = await build_context(_turn(attachments=(att,), window=100), ChatConfig())
    assert built.messages[-1].attachments == (att,)
