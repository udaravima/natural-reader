# v2.3 Task G report: an evaluation harness a person can run against their own model

## Change
- **`server/evals/doc_qa.py`.**
  - `build_fixture()`:
    - a fixed-seed, 10-page text document (40 sentences per reader page, as `extract_text` paginates) of neutral filler;
    - planted facts in the middle of chosen pages;
    - `doc_id` is the SHA-256 of the text.
  - Cases:
    - single-hop: the codename, page 2;
    - two-hop: the ZEPHYR-9 dataset on page 3, collected by Aldermoor on page 8;
    - exact-label: Table 7.3, page 6;
    - page-read: "what does page 5 say", page 5;
    - absent: the 2030 capital budget, whose terms are nowhere in the text.
  - Scoring (pure):
    - `cited_pages` uses the SPA's citation regex;
    - `is_refusal` covers doesn't cover / mention, couldn't find, not mentioned, no information about, including curly apostrophes;
    - `score` passes a fact case on the fact plus every page cited, and the absent case on a refusal with no page cited (a cited page counts as invented); it also records tools and rounds;
    - `format_report` gives one PASS or FAIL line per case, then "n/N passed".
  - `run_eval(model)` does the live run:
    1. starts the database pool, the embedding client and the LLM router, as the app does;
    2. provisions an eval user (`doc-qa-eval@example.com`);
    3. writes the fixture to `DOC_STORAGE_DIR`, inserts the document and a library entry (idempotent), then `run_pipeline` and `run_rebuild` (both no-ops when current);
    4. asks each question through `run_turn` with the real config in a fresh session, collecting the text and `tool-input-available` events;
    5. deletes the session.

    The document stays indexed, so the next run doesn't re-embed it.
- **`scripts/eval_doc_qa.py`.** The CLI takes `--model` and `--show-answers`, loads `.env` if python-dotenv is present, prints the report, and exits 0 only when every case passed.
- **Docs.**
  - CHAT_WITH_PDF §6.5: what each case tests, how to run it, and when to re-run (after changing a model, a prefix, `CHAT_SEARCH_MIN_SCORE` or the chunk sizes; the thresholds were measured before the prefixes).
  - README Scripts.
  - CHANGELOG Added.
  - CHAT_WITH_PDF and `.env.example` were already brought up to date task by task.

## Tests
`server/tests/test_eval_doc_qa.py`, 13 tests, all on the pure parts:
- every fact sits on the page its case reports, checked with the real `extract_text`;
- the fixture is deterministic and covers all five kinds;
- the two-hop pages are at least 3 apart;
- the absent terms aren't in the text;
- `cited_pages`, with page 0 ignored;
- refusal recognition;
- fact-case scoring: a missing page or a wrong fact fails;
- absent scoring: an invented page fails;
- tools and rounds are recorded;
- the report format.

**Wiring smoke run** (sandbox, not committed): `run_eval` against a throwaway `eval_smoke` database, with deterministic fake embeddings and a scripted FakeRouter (one search round, then "It is BLUE HERON (page 2).").
- Result: 1/5, exactly as the script implies: single-hop passed, the absent case caught the invented page 2.
- The database showed the document `indexed` with a profile, 10 pages and 30 split chunks, and no chat sessions left behind.
- The database was dropped afterwards.

## Not verified here
There is no real model in this sandbox. The live run is for the local machine; its first use will also re-measure the Task B relevance thresholds under the prefixes.

## Suites
- backend: 772 passed;
- vitest: exit 0;
- eslint: clean.

## Fix round 1 — FIX_BASE 29e4cb8

- **C1: the eval took over an unclaimed seed admin.**
  - `eval_user(conn)` looks the eval identity up by (iss, sub). Failing that, it creates its own row with `enroll_linked_user` (chat and reader only, active). It never goes through the OIDC resolver.
  - DB test: the seed admin row is unchanged, the eval user is a different row with no admin capability, and a second call returns the same row. It failed before the fix (no function).
  - Smoke re-run on a scratch database: the seed admin keeps `admin@localhost` with no OIDC sub, and the eval runs as `doc-qa-eval@example.com` with chat and reader.
- **I2: startup parity with the app.** `run_eval` now:
  - starts and stops the web-search client;
  - drains the orchestrator's background tasks before `close_db`;
  - starts everything inside the `try`.
- **I3: sessions left behind.** Each case's turn runs inside `try/finally`, and the generator runs under `aclosing`. The session is deleted on success, on an error and on Ctrl-C.
- **I4: the refusal check.**
  - The regex accepts n't/not/never before mentioned / covered / provide / specify / give / state, and also "nothing about", "says nothing" and "don't have information". Curly apostrophes and hyphen variants are normalised.
  - The absent case also fails on any figure other than the question's own year or a cited page.
  - Tests: 6 phrasings in the model's own words, and 2 "refusal followed by a made-up figure" answers. All failed before the fix.
- **M5: fact matching.**
  - `has_term` matches whole words, any hyphen (U+2010-2015, U+2212) and alternatives ("a|b").
  - Expected terms: "41 ms|41ms|41 milliseconds" and "nine weeks|9 weeks|nine-week".
  - Tests: 410 ms and 141 ms don't match; 9 weeks and the non-breaking-hyphen form of ZEPHYR-9 do.
- **M6 and M7.** Documented in §6.5: the page-read case tests the fact only, and the absent case is strict about any cited page.
- **M8: exit codes.**
  - `init_db()` returning False raises `EvalSetupError`, and so does a fixture that didn't index.
  - A turn that ended in an error event is reported as ERROR, not as a model failure.
  - The CLI exits 0 when everything passed, 1 when the model failed a case, 2 when the setup failed or any case errored. Test: the ERROR result and report line.
- **M9: route parity.** The model id goes through `router.canonical_id`, a model the deployment doesn't allow is refused with `EvalSetupError`, and the deployment's daily budget is passed.
- **M10.** `SENTENCES_PER_PAGE` is imported from `extract`. Test added.

Suites:
- backend: 788 passed;
- vitest: exit 0;
- eslint: clean.
