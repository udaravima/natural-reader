"""v2.3 Task G: the document-QA evaluation harness. The live run needs
Postgres, an embedding model and a chat model (scripts/eval_doc_qa.py); the
fixture and the scoring are pure, and tested here."""
from __future__ import annotations

import pytest

from server.evals import doc_qa
from server.services import extract


def test_the_fixture_plants_every_fact_on_the_page_it_reports():
    fixture = doc_qa.build_fixture()
    pages = {c.page: c.text for c in extract.extract_text(fixture.text).chunks}
    assert len(pages) >= 8                                         # long enough that pages matter
    for case in fixture.cases:
        for fact, page in zip(case.facts, case.pages):
            assert fact in pages[page], (case.name, fact, page)


def test_the_fixture_covers_each_journey_and_is_the_same_every_time():
    a, b = doc_qa.build_fixture(), doc_qa.build_fixture()
    assert a.text == b.text and a.doc_id == b.doc_id
    kinds = {c.kind for c in a.cases}
    assert kinds == {"single-hop", "two-hop", "exact-label", "page-read", "absent",
                     "small-talk", "general", "live", "follow-up"}
    two_hop = next(c for c in a.cases if c.kind == "two-hop")
    assert len(set(two_hop.pages)) == 2 and abs(two_hop.pages[0] - two_hop.pages[1]) >= 3
    absent = next(c for c in a.cases if c.kind == "absent")
    assert not absent.pages and not any(t.lower() in a.text.lower() for t in absent.expect)


@pytest.mark.parametrize("answer,pages", [
    ("It is BLUE HERON (page 4).", {4}),
    ("See page 2 and (Page 11); also page 0.", {2, 11}),
    ("No citation here.", set()),
])
def test_cited_pages_reads_what_the_app_links(answer, pages):
    assert doc_qa.cited_pages(answer) == pages


@pytest.mark.parametrize("answer,refused", [
    ("The document doesn't seem to cover the 2030 capital budget.", True),
    ("I couldn't find anything about that in the document.", True),
    ("The report does not mention a capital budget.", True),
    ("The capital budget is 4 million (page 3).", False),
])
def test_a_refusal_is_recognised(answer, refused):
    assert doc_qa.is_refusal(answer) == refused


def _case(kind, expect, pages):
    return doc_qa.Case(name="c", kind=kind, question="q", facts=("f",) * len(pages), pages=tuple(pages),
                       expect=tuple(expect))


def test_a_fact_case_needs_the_fact_and_every_page():
    case = _case("two-hop", ["zephyr-9", "aldermoor"], [3, 9])
    ok = doc_qa.score(case, "It uses ZEPHYR-9 (page 3), collected by Aldermoor (page 9).", [])
    assert ok.passed and ok.fact and ok.cited
    no_page = doc_qa.score(case, "It uses ZEPHYR-9 (page 3), collected by Aldermoor.", [])
    assert no_page.fact and not no_page.cited and not no_page.passed
    wrong = doc_qa.score(case, "It uses MIMIC (page 3) (page 9).", [])
    assert not wrong.fact and not wrong.passed


def test_an_absent_case_needs_a_refusal_and_no_invented_page():
    case = _case("absent", ["capital budget"], [])
    assert doc_qa.score(case, "The document doesn't seem to cover that.", []).passed
    invented = doc_qa.score(case, "The document doesn't seem to cover it, but see (page 4).", [])
    assert not invented.passed and invented.invented_pages == {4}
    assert not doc_qa.score(case, "It is 3 million.", []).passed


def test_the_score_records_tools_and_rounds():
    calls = [{"toolName": "search_documents", "round": 1}, {"toolName": "read_document_pages", "round": 2},
             {"toolName": "search_documents", "round": 2}]
    result = doc_qa.score(_case("single-hop", ["x"], [1]), "x (page 1)", calls)
    assert result.tools == ["search_documents", "read_document_pages", "search_documents"]
    assert result.rounds == 2


def test_the_report_is_one_line_per_case_and_a_total():
    case = _case("single-hop", ["x"], [1])
    results = [doc_qa.score(case, "x (page 1)", []), doc_qa.score(case, "nope", [])]
    report = doc_qa.format_report([results], model="ollama:llama3.2:3b")
    lines = report.splitlines()
    assert "ollama:llama3.2:3b" in lines[0]
    assert sum("PASS" in l for l in lines) == 1 and sum("FAIL" in l for l in lines) == 1
    assert lines[-1].startswith("1/2 passed")


# ---- Task G review fix round 1 ----

async def test_the_eval_user_is_its_own_row_and_never_claims_the_seed_admin(db_conn):
    """Review C1: the OIDC resolver binds a new identity to an unclaimed seed
    admin. On a dev-bypass machine (the one this eval is for) that is the
    person's own admin account."""
    from server.auth.users import SEED_ADMIN_ID
    cur = await db_conn.execute("SELECT oidc_sub, email FROM users WHERE id = %s", (SEED_ADMIN_ID,))
    before = await cur.fetchone()
    first = await doc_qa.eval_user(db_conn)
    again = await doc_qa.eval_user(db_conn)
    assert first == again != SEED_ADMIN_ID
    cur = await db_conn.execute("SELECT oidc_sub, email FROM users WHERE id = %s", (SEED_ADMIN_ID,))
    assert await cur.fetchone() == before
    cur = await db_conn.execute("SELECT capabilities, status FROM users WHERE id = %s", (first,))
    caps, status = await cur.fetchone()
    assert "admin" not in caps and status == "active"


@pytest.mark.parametrize("answer", [
    "The capital budget for 2030 isn't mentioned in the document.",
    "The document does not provide a 2030 capital budget.",
    "The report doesn't specify any capital budget.",
    "There is nothing about a capital budget in it.",
    "The report says nothing about it.",
    "I don't have information on that from this document.",
])
def test_refusals_in_the_models_own_words_are_recognised(answer):
    """Review I4."""
    assert doc_qa.is_refusal(answer)


@pytest.mark.parametrize("answer", [
    "Not mentioned in the text, but it is 4 million.",
    "The budget is 4m; earlier drafts did not include it.",
])
def test_a_refusal_followed_by_a_made_up_figure_fails(answer):
    case = _case("absent", ["capital budget", "2030"], [])
    assert not doc_qa.score(case, answer, []).passed


@pytest.mark.parametrize("term,answer,found", [
    ("41 ms|41ms|41 milliseconds", "a median of 41 ms", True),
    ("41 ms|41ms|41 milliseconds", "a median of 410 ms", False),
    ("41 ms|41ms|41 milliseconds", "a median of 141 ms", False),
    ("nine weeks|9 weeks|nine-week", "It ran for 9 weeks.", True),
    ("zephyr-9", "the ZEPHYR‑9 dataset", True),                   # a non-breaking hyphen
])
def test_facts_match_whole_words_and_any_hyphen(term, answer, found):
    """Review M5."""
    assert doc_qa.has_term(answer, term) == found


def test_a_turn_that_errored_is_reported_as_an_error_not_a_model_failure():
    """Review M8."""
    case = _case("single-hop", ["x"], [1])
    result = doc_qa.score(case, "", [], error="provider_error")
    assert not result.passed and result.error == "provider_error"
    assert "ERROR" in doc_qa.format_report([[result]], model="m")


def test_the_fixture_page_size_is_the_extractors():
    from server.services import extract
    assert doc_qa.SENTENCES_PER_PAGE is extract.SENTENCES_PER_PAGE


def test_a_non_breaking_space_and_9_week_match_too():
    """Re-review minors."""
    assert doc_qa.has_term("a median of 41 ms", "41 ms|41ms|41 milliseconds")
    assert doc_qa.has_term("a 9-week trial", "nine weeks|9 weeks|nine-week|9-week")


async def test_an_eval_identity_bound_to_an_admin_is_refused(db_conn):
    """Re-review minor: a database an earlier eval build touched has the seed
    admin bound to the eval identity; never run as that admin."""
    from server.auth.users import SEED_ADMIN_ID
    await db_conn.execute("UPDATE users SET oidc_iss = %s, oidc_sub = %s WHERE id = %s",
                          (doc_qa.EVAL_ISS, doc_qa.EVAL_SUB, SEED_ADMIN_ID))
    with pytest.raises(doc_qa.EvalSetupError):
        await doc_qa.eval_user(db_conn)


def test_the_eval_script_loads_dotenv_before_any_server_module():
    """Final review I1: server modules read the chunk and embedding settings
    when they're imported, so importing one before load_dotenv() ignored .env."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    probe = ("import importlib.util, sys\n"
             f"spec = importlib.util.spec_from_file_location('ev', {str(root / 'scripts' / 'eval_doc_qa.py')!r})\n"
             "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
             "print(sorted(m for m in sys.modules if m == 'server' or m.startswith('server.')))\n")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=root, check=True)
    assert out.stdout.strip() == "[]"


# ---- v2.4 Task 0: the eval checks the strategy too ----

def _kind(kind):
    return next(c for c in doc_qa.build_fixture().cases if c.kind == kind)


def test_the_new_cases_exist_and_follow_up_has_its_setup_turn():
    cases = {c.kind: c for c in doc_qa.build_fixture().cases}
    assert {"small-talk", "general", "live", "follow-up"} <= cases.keys()
    assert cases["follow-up"].setup == ("What is the project's codename?",)
    assert all(c.setup == () for k, c in cases.items() if k != "follow-up")


def test_small_talk_passes_only_without_tools_or_pages():
    case = _kind("small-talk")
    assert doc_qa.score(case, "You're welcome!", []).passed
    assert not doc_qa.score(case, "You're welcome!", [{"toolName": "search_documents", "round": 1}]).passed
    assert not doc_qa.score(case, "Glad page 2 helped (page 2).", []).passed


def test_general_knowledge_needs_the_fact_and_no_page():
    case = _kind("general")
    assert doc_qa.score(case, "In 1969.", []).passed
    assert not doc_qa.score(case, "In 1969 (page 4).", []).passed
    assert not doc_qa.score(case, "I'm not sure.", []).passed


def test_live_needs_web_search_and_is_skipped_without_it():
    case = _kind("live")
    assert doc_qa.score(case, "It's 4°C.", [{"toolName": "web_search", "round": 1}]).passed
    assert not doc_qa.score(case, "The trial was in Tromsø (page 5).",
                            [{"toolName": "search_documents", "round": 1}]).passed
    skipped = doc_qa.score(case, "", [], web_offered=False)
    assert skipped.skipped == "web_search not offered" and not skipped.passed
    assert "SKIP" in doc_qa.format_report([[skipped]], model="m")


def test_follow_up_passes_without_a_new_tool_call():
    case = _kind("follow-up")
    assert doc_qa.score(case, "Blue Heron.", []).passed
    assert not doc_qa.score(case, "Blue Heron.", [{"toolName": "search_documents", "round": 1}]).passed
    assert not doc_qa.score(case, "I don't remember.", []).passed


def test_a_fact_found_without_the_journey_is_marked():
    case = _kind("page-read")
    r = doc_qa.score(case, "It ran for nine weeks (page 5).", [], prefetched=False)
    assert r.passed and r.trail is False
    assert "PASS (no trail)" in doc_qa.format_report([[r]], model="m")
    assert doc_qa.score(case, "It ran for nine weeks (page 5).", [], prefetched=True).trail is True


def test_the_report_shows_seconds_and_a_tally_over_repeats():
    case = _kind("single-hop")
    a = doc_qa.score(case, "BLUE HERON (page 2).", [{"toolName": "search_documents", "round": 1}])
    b = doc_qa.score(case, "BLUE HERON.", [{"toolName": "search_documents", "round": 1}])
    a.seconds, b.seconds = 4.2, 3.0
    report = doc_qa.format_report([[a], [b]], model="m")
    assert "4.2s" in report and "single-hop 1/2" in report
    assert report.splitlines()[-1].startswith("1/2 passed")


def test_the_script_has_think_and_repeat_flags():
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "eval_doc_qa.py"
    out = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True).stdout
    assert "--think" in out and "--repeat" in out


def test_the_script_can_switch_the_prefetch_for_a_run():
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "eval_doc_qa.py"
    out = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True).stdout
    assert "--prefetch" in out


# ---- v2.4 Task D: chips count as cited; inline is still reported ----

def test_chip_pages_count_as_cited_and_say_so():
    case = _kind("two-hop")
    calls = [{"toolName": "search_documents", "round": 1}]
    by_chips = doc_qa.score(case, "ZEPHYR-9, collected by the Aldermoor Institute.", calls, chip_pages=[3, 8])
    assert by_chips.passed and by_chips.cited and not by_chips.cited_inline
    assert "pages ok (chips)" in doc_qa.format_report([[by_chips]], model="m")
    inline = doc_qa.score(case, "ZEPHYR-9 (page 3), Aldermoor (page 8).", calls, chip_pages=[3, 8])
    assert inline.cited_inline and "pages ok (inline)" in doc_qa.format_report([[inline]], model="m")
    assert not doc_qa.score(case, "ZEPHYR-9, Aldermoor.", calls, chip_pages=[3]).passed


def test_the_report_gives_the_inline_citation_rate():
    case = _kind("single-hop")
    calls = [{"toolName": "search_documents", "round": 1}]
    a = doc_qa.score(case, "BLUE HERON (page 2).", calls, chip_pages=[2])
    b = doc_qa.score(case, "BLUE HERON.", calls, chip_pages=[2])
    assert "inline citations 1/2" in doc_qa.format_report([[a, b]], model="m")


# ---- C2.1 Task 13: the run is in the web mode this evaluation measures, and puts the environment back ----

async def test_run_eval_uses_auto_mode_unless_the_environment_says_otherwise_and_puts_it_back(monkeypatch):
    import os
    seen = []

    async def fake_inner(model, *, show_answers, think, repeat):
        seen.append(os.environ.get("CHAT_WEB_SEARCH"))
        if model == "boom":
            raise doc_qa.EvalSetupError("no database")
        return []

    monkeypatch.setattr(doc_qa, "_run_eval", fake_inner)
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    await doc_qa.run_eval("m")
    assert seen == ["auto"] and "CHAT_WEB_SEARCH" not in os.environ            # not left behind
    monkeypatch.setenv("CHAT_WEB_SEARCH", "ask")                               # an explicit setting wins
    await doc_qa.run_eval("m")
    assert seen == ["auto", "ask"] and os.environ["CHAT_WEB_SEARCH"] == "ask"
    monkeypatch.delenv("CHAT_WEB_SEARCH")
    with pytest.raises(doc_qa.EvalSetupError):                                 # ... and after a failed run too
        await doc_qa.run_eval("boom")
    assert seen[-1] == "auto" and "CHAT_WEB_SEARCH" not in os.environ


# ---- C2.1 Task 14: the strategy layer of the evaluation (ask mode, offers) ----

OFFER = [{"toolName": "offer_web_search", "round": 1}]
SEARCH_WEB = [{"toolName": "web_search", "round": 1}]


def test_live_in_ask_mode_passes_on_an_offer_and_no_search():
    """In ask mode the model has no web_search: asking first IS the right answer to a live question. The offer
    ends the turn, so it may have written nothing."""
    case = _kind("live")
    assert doc_qa.score(case, "Shall I look that up?", OFFER, mode="ask").passed
    assert doc_qa.score(case, "", OFFER, mode="ask").passed
    assert not doc_qa.score(case, "It's 4°C.", SEARCH_WEB, mode="ask").passed             # searched without asking
    assert not doc_qa.score(case, "", [*OFFER, *SEARCH_WEB], mode="ask").passed           # ... even after offering
    assert not doc_qa.score(case, "I can't look that up here.", [], mode="ask").passed    # no offer
    assert not doc_qa.score(case, "The trial was in Tromsø (page 5).",
                            [{"toolName": "search_documents", "round": 1}], mode="ask").passed


def test_live_in_auto_mode_is_scored_as_before():
    case = _kind("live")
    assert doc_qa.score(case, "It's 4°C.", SEARCH_WEB, mode="auto").passed
    assert doc_qa.score(case, "It's 4°C.", SEARCH_WEB).passed                               # auto is the default
    assert not doc_qa.score(case, "Shall I look that up?", OFFER, mode="auto").passed       # no such tool in auto mode


def test_live_is_skipped_when_web_search_is_off():
    skipped = doc_qa.score(_kind("live"), "", [], mode="off")
    assert skipped.skipped == "web search is off" and not skipped.passed
    assert doc_qa.score(_kind("small-talk"), "You're welcome!", [], mode="off").passed      # other cases are asked


def test_live_report_says_what_the_model_did_in_ask_mode():
    case = _kind("live")
    offered = doc_qa.score(case, "Shall I?", OFFER, mode="ask")
    searched = doc_qa.score(case, "4°C", SEARCH_WEB, mode="ask")
    none = doc_qa.score(case, "no", [], mode="ask")
    report = doc_qa.format_report([[offered, searched, none]], model="m")
    assert "offered the search" in report and "searched without asking" in report and "didn't offer" in report


def test_an_offer_does_not_fail_a_refusal_but_an_invented_page_still_does():
    """Task 14: an `absent` answer passes on a refusal or an offer that invents no page or figure."""
    case = _case("absent", ["capital budget", "2030"], [])
    ok = doc_qa.score(case, "The document doesn't seem to cover that. Shall I search the web?", OFFER, mode="ask")
    assert ok.passed and ok.offered and "refused (offered the search)" in doc_qa.format_report([[ok]], model="m")
    no_offer = doc_qa.score(case, "The document doesn't seem to cover that.", [], mode="ask")
    assert no_offer.passed and not no_offer.offered
    paged = doc_qa.score(case, "The document doesn't seem to cover it, but see (page 4). Shall I search?", OFFER)
    assert not paged.passed and paged.invented_pages == {4}
    figure = doc_qa.score(case, "Not covered here. It is probably 3 million. Shall I search?", OFFER)
    assert not figure.passed and figure.invented_figure
    # The offer is itself the answer to a gap (ruling 8), even when the turn wrote nothing before it.
    assert doc_qa.score(case, "", OFFER, mode="ask").passed
    assert doc_qa.score(case, "Shall I look that up?", OFFER, mode="ask").passed
    report = doc_qa.format_report([[doc_qa.score(case, "", OFFER, mode="ask")]], model="m")
    assert "offered the search (wrote no text)" in report
    # ... but not an offer next to a made-up page or figure, and not neither a refusal nor an offer.
    assert not doc_qa.score(case, "See (page 4).", OFFER, mode="ask").passed
    assert not doc_qa.score(case, "It is 3 million.", OFFER, mode="ask").passed
    assert not doc_qa.score(case, "", []).passed
    assert not doc_qa.score(case, "The budget is large.", []).passed


def test_the_runs_web_mode_is_the_deployments_resolution_of_the_environment(monkeypatch):
    monkeypatch.setenv("CHAT_WEB_SEARCH", "ASK")
    assert doc_qa.current_web_mode() == "ask"
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")
    assert doc_qa.current_web_mode() == "auto"
    monkeypatch.setenv("CHAT_WEB_SEARCH", "nonsense")      # an invalid value is ignored: the default asks first
    assert doc_qa.current_web_mode() == "ask"


# ---- the script's guards: a scratch database, a storage folder of its own (as eval_project_qa.py) ----

def _script():
    """scripts/eval_doc_qa.py as a module (it is not on the import path)."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "eval_doc_qa.py"
    spec = importlib.util.spec_from_file_location("eval_doc_qa_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_doc_script_refuses_the_real_database():
    script = _script()
    with pytest.raises(SystemExit):
        script.check_database_url("postgresql://u:p@localhost:5433/natural_reader")
    script.check_database_url("postgresql://u:p@localhost:5433/natural_reader_eval")


@pytest.mark.parametrize("url", [
    None, "",                                                   # unset: the server would use its default, the real one
    "postgresql://u:p@localhost:5433/natural_reader",
    "postgresql://u:p@localhost:5433/natural_reader?sslmode=disable",
    "postgresql://u:p@localhost:5433/",                         # no database named
    "dbname=natural_reader user=u host=localhost",              # the keyword form of the same
    "not a connection string at all ::",
])
def test_the_doc_script_refuses_anything_but_a_named_scratch_database(url, capsys):
    with pytest.raises(SystemExit) as stopped:
        _script().check_database_url(url)
    assert stopped.value.code == 2
    assert "u:p" not in capsys.readouterr().err      # the URL holds a password: never echoed


@pytest.mark.parametrize("url", ["postgresql://u:p@localhost:5434/natural_reader_eval",
                                 "dbname=natural_reader_eval host=localhost"])
def test_the_doc_script_accepts_a_scratch_database(url):
    _script().check_database_url(url)


def test_the_doc_eval_gets_a_fresh_temp_storage_folder_whatever_is_exported(monkeypatch):
    import os
    import tempfile
    from pathlib import Path
    from server.services import doc_storage
    monkeypatch.setenv("DOC_STORAGE_DIR", "/the/apps/real/storage")
    monkeypatch.setenv("PDF_STORAGE_DIR", "/the/apps/old/storage")
    with _script().eval_storage(None) as folder:
        assert folder.is_dir() and folder.name.startswith("nr-eval-storage-")
        assert folder.parent == Path(tempfile.gettempdir())
        assert doc_storage.storage_dir() == folder.resolve()      # what the pipeline will really use
        (folder / "x.txt").write_text("kept until the block ends")
    assert not folder.exists()
    assert os.environ["DOC_STORAGE_DIR"] == "/the/apps/real/storage"   # the exported value is put back


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_the_doc_temp_storage_folder_is_removed_on_an_error_or_ctrl_c(failure, monkeypatch):
    monkeypatch.delenv("DOC_STORAGE_DIR", raising=False)
    seen = []
    with pytest.raises(failure):
        with _script().eval_storage(None) as folder:
            seen.append(folder)
            (folder / "doc.txt").write_text("x")
            raise failure()
    assert seen and not seen[0].exists()


_DOC_MAIN_PROBE = """
import asyncio, dotenv, importlib.util, json, os, sys
from pathlib import Path
real = dotenv.load_dotenv
dotenv.load_dotenv = lambda *a, **k: real(Path(os.environ["PROBE_DOTENV"]), **k)    # a .env of the test's own
spec = importlib.util.spec_from_file_location("ev", os.environ["PROBE_SCRIPT"])
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
import server.evals.doc_qa as dq
from server.services import doc_storage
seen = {}
async def fake_run_eval(model, **kw):
    seen["dir"] = str(doc_storage.storage_dir())
    seen["exists"] = doc_storage.storage_dir().is_dir()
    return []
dq.run_eval = fake_run_eval
sys.argv = ["eval_doc_qa.py", "--model", "m", *json.loads(os.environ.get("PROBE_ARGV", "[]"))]
rc = script.main()
print(json.dumps({"rc": rc, **seen, "after": os.path.isdir(seen["dir"])}))
"""


REAL_URL = "postgresql://u:p@h:5433/natural_reader"


def _run_doc_main(tmp_path, database_url, *argv, dotenv_url=REAL_URL):
    """The script's real main(), with a fake run_eval that records where storage points, in a subprocess
    (main loads a .env into os.environ). The test's .env and the environment both name another folder.
    `database_url`: DATABASE_URL as exported (None: not exported); `dotenv_url`: DATABASE_URL in the .env
    (None: the .env has none). By default the .env names the real database, as a developer's may."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    lines = ["DOC_STORAGE_DIR=/from/dotenv", "PDF_STORAGE_DIR=/from/dotenv/pdfs"]
    if dotenv_url:
        lines.append(f"DATABASE_URL={dotenv_url}")
    (tmp_path / ".env").write_text("\n".join(lines) + "\n")
    import json
    env = {**os.environ, "PROBE_DOTENV": str(tmp_path / ".env"),
           "PROBE_SCRIPT": str(root / "scripts" / "eval_doc_qa.py"), "PROBE_ARGV": json.dumps(list(argv)),
           "DOC_STORAGE_DIR": "/exported/by/the/caller", "PYTHONPATH": str(root)}
    env.pop("DATABASE_URL", None)
    if database_url:
        env["DATABASE_URL"] = database_url
    return subprocess.run([sys.executable, "-c", _DOC_MAIN_PROBE], capture_output=True, text=True, cwd=root, env=env)


def test_main_runs_the_doc_eval_in_a_temp_storage_folder_and_removes_it(tmp_path):
    import json
    import tempfile
    from pathlib import Path
    out = _run_doc_main(tmp_path, "postgresql://u:p@localhost:5434/natural_reader_eval")
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen["rc"] == 0 and seen["exists"]
    assert Path(seen["dir"]).parent == Path(tempfile.gettempdir()).resolve()
    assert Path(seen["dir"]).name.startswith("nr-eval-storage-")
    assert not seen["after"]
    assert seen["dir"] not in ("/exported/by/the/caller", "/from/dotenv")


def test_main_honours_storage_dir_for_the_doc_eval_and_keeps_it(tmp_path):
    import json
    from pathlib import Path
    mine = tmp_path / "kept"
    out = _run_doc_main(tmp_path, "postgresql://u:p@localhost:5434/natural_reader_eval", "--storage-dir", str(mine))
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen["dir"] == str(Path(mine).resolve()) and seen["exists"] and seen["after"]


@pytest.mark.parametrize("exported,dotenv", [
    ("postgresql://u:p@localhost:5434/natural_reader", REAL_URL),   # exported, real (and the .env's too)
    (None, REAL_URL),                                               # the real URL is only in .env
    (None, None),                                                   # unset everywhere: the server would use its default
])
def test_main_refuses_the_real_database_before_anything_runs(tmp_path, exported, dotenv):
    """Whether the real database comes from the environment or only from .env, or no database is named
    at all, the run never starts and the password is not echoed."""
    out = _run_doc_main(tmp_path, exported, dotenv_url=dotenv)
    assert out.returncode == 2 and "Refusing to run" in out.stderr, out.stderr
    assert "u:p" not in out.stderr and out.stdout.strip() == ""


def test_an_exported_scratch_url_wins_over_a_real_one_in_dotenv(tmp_path):
    """dotenv fills only what the environment lacks, so a developer's .env naming natural_reader does not
    send an eval with an exported scratch URL to the real database."""
    out = _run_doc_main(tmp_path, "postgresql://u:p@localhost:5434/natural_reader_eval", dotenv_url=REAL_URL)
    assert out.returncode == 0, out.stderr
