# Project chat evaluation: baseline and results

What this file is: the scoreboard for C2.1 "project chat". The evaluation asks a chat model
13 questions (15 in the 50-document variant) about a project of documents, through the real
chat turn path scoped to that project, and scores each answer on the fact, the right document
(cited as "(Name, page N)", the form the project score reads), the right page, and the
web-search rules. Every later layer (map, balance, code chunks, rules, web offer) is compared
with the numbers below, so the baseline section is never edited: later runs are appended as
new sections.

The code is `server/evals/project_qa.py` (cases, scoring, `run_eval`); the script is
`scripts/eval_project_qa.py`; `server/tests/test_eval_project_qa.py` tests the pure parts, the
seeding and the script's database guard.

## How to run it

The eval writes an eval user, a project, its documents and chats into the database it is
given. **Always point it at a scratch database, never the real one.** The script refuses to
start when `DATABASE_URL` is unset or names the database `natural_reader`, and an exported
`DATABASE_URL` wins over `.env` (the file only fills in what the environment lacks).

```sh
# once: a scratch database on any Postgres with pgvector (a throwaway one is best)
PGPASSWORD=... psql -h 127.0.0.1 -p 5434 -U natural_reader -d postgres -c "CREATE DATABASE natural_reader_eval"

DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_eval \
  .venv/bin/python scripts/eval_project_qa.py --model ollama:gemma4:31b-cloud --variant 9 --repeat 3
```

It needs the embedding model (Ollama `nomic-embed-text`) and the chat model's provider, set by
the same environment as the backend. Flags: `--variant 9|50` (the nine fixture documents, or
plus 41 padding documents; the map-shortened cases run only in 50), `--repeat N` (1-10; adds a
per-kind tally), `--think off|on`, `--show-answers`. Progress goes to stderr, the report to
stdout. Exit status: 0 every case passed, 1 a case failed, 2 the harness couldn't run
(database, embeddings, provider, or a turn ended in an error). Documents stay indexed between
runs; the project is made anew each run. The documents' files (named by hash) never touch your
real storage folder: the script sets `DOC_STORAGE_DIR` to a temporary folder of its own
(`nr-eval-storage-*`, whatever `.env` or the environment say) and removes it at the end, also on
an error or Ctrl-C. `--storage-dir PATH` uses and keeps another folder instead. **One run is noisy** (see below): compare layers on
`--repeat 3` or more.

## Baseline (scope widened only)

- Date: 2026-10-18
- Commit: `2ad554d` (C2.1 Task 3, the scope plumbing) plus the Task 4 runner, committed with
  this file. No map, no balance, no code chunks; the rules are unchanged.
- Model: `ollama:gemma4:31b-cloud`, thinking off, a scratch database.
- Web: `web_search` is configured on this machine but SearXNG isn't running, so both calls in
  the `live` case failed with a connection error ("web calls" counts calls attempted).
- Headline: variant 9 passed 3/13 in its first run and 6/39 over three runs (about 2 of 13
  per run); right-document citations 3/11 and 6/33. Variant 50 passed 1/15.

### Variant 9, one run

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
FAIL  overview       features               6.0s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
PASS  procedure      deploy-and-test        5.0s rounds=3 tools=search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok
FAIL  code           start-command          2.1s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     2.4s rounds=1 tools=search_documents  fact missing (./startup.sh quickstart); not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.4s rounds=1 tools=search_documents  fact missing (python -m pytest server/tests, postgres); not cited (TESTING.md); page missing (want TESTING.md p. 1)
PASS  exact-label    session-lifetimes      1.3s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
FAIL  exact-label    oidc-issuer            2.6s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        4.6s rounds=3 tools=search_documents,search_documents,read_document_pages  fact missing (each message|every message); not cited (USER_GUIDE.md); cited the wrong document (CHAT_WITH_PDF.md, README.md)
FAIL  named-doc      deployment-uploads     2.8s rounds=2 tools=search_documents,read_document_pages  fact missing (client_max_body_size, 100m|100 mb|100mb); not cited (DEPLOYMENT.md)
FAIL  balance        next-work              1.6s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.3s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          0.7s rounds=0 tools=-  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.6s rounds=2 tools=web_search,web_search  no offer; 2 web calls
right-document citations 3/11
web calls 2
3/13 passed
```

### Variant 9, three runs (the reference to compare with)

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
-- run 1
FAIL  overview       features               4.8s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        4.1s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.3s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     1.6s rounds=1 tools=search_documents  fact missing (./startup.sh quickstart); not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.4s rounds=1 tools=search_documents,search_documents  fact missing (python -m pytest server/tests, postgres); not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.5s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.0s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        5.9s rounds=5 tools=search_documents,search_documents,read_document_pages,search_documents,read_document_pages  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     3.8s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
PASS  balance        next-work              1.7s rounds=1 tools=search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.3s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          3.2s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.0s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 2
FAIL  overview       features               7.1s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        6.5s rounds=3 tools=search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.4s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     1.6s rounds=1 tools=search_documents  fact missing (./startup.sh quickstart); not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.6s rounds=1 tools=search_documents  fact missing (python -m pytest server/tests, postgres); not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.5s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.0s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        3.4s rounds=3 tools=search_documents,search_documents,read_document_pages  fact missing (each message|every message); not cited (USER_GUIDE.md); cited the wrong document (CHAT_WITH_PDF.md, README.md)
FAIL  named-doc      deployment-uploads     2.8s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
PASS  balance        next-work              1.5s rounds=1 tools=search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.2s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          2.9s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         1.9s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 3
FAIL  overview       features               4.7s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        4.5s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.2s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     1.4s rounds=1 tools=search_documents  fact missing (./startup.sh quickstart); not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.3s rounds=1 tools=search_documents  fact missing (python -m pytest server/tests, postgres); not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.4s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.1s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        3.6s rounds=3 tools=search_documents,search_documents,read_document_pages  fact missing (each message|every message); not cited (USER_GUIDE.md); cited the wrong document (CHAT_WITH_PDF.md, README.md)
FAIL  named-doc      deployment-uploads     2.3s rounds=2 tools=search_documents,read_document_pages  fact missing (client_max_body_size, 100m|100 mb|100mb); not cited (DEPLOYMENT.md)
PASS  balance        next-work              1.4s rounds=1 tools=search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.2s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          4.4s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; said the documents don't cover it; no offer
FAIL  live           ollama-release         1.8s rounds=2 tools=web_search,web_search  no offer; 2 web calls
tally: overview 0/3, procedure 0/3, code 0/9, exact-label 0/6, named-doc 0/6, balance 6/6, gap-general 0/3, live 0/3
right-document citations 6/33
web calls 6
6/39 passed
```

### Variant 50, one run

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 50)
FAIL  overview       features               5.2s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        5.3s rounds=3 tools=search_documents,read_document_pages,read_document_pages,read_document_pages  fact missing (./startup.sh start, pytest server/tests); not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.4s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     2.3s rounds=1 tools=search_documents  fact missing (./startup.sh quickstart); not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.1s rounds=1 tools=search_documents  fact missing (python -m pytest server/tests, postgres); not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.7s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            3.1s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        5.1s rounds=5 tools=search_documents,search_documents,read_document_pages,search_documents,read_document_pages  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     1.6s rounds=1 tools=search_documents  fact missing (client_max_body_size, 100m|100 mb|100mb); not cited (DEPLOYMENT.md)
FAIL  balance        next-work              1.5s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   2.0s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          0.7s rounds=0 tools=-  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         3.2s rounds=2 tools=web_search,web_search  no offer; 2 web calls
FAIL  map-shortened  features-50            6.0s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
FAIL  map-shortened  start-command-50       1.7s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
right-document citations 1/13
web calls 2
1/15 passed
```

### What failed and why

The scope is wider, but nothing else changed, and the rules still talk about "the open
document" (`rhel-packages` answers "I can check the open document"): the model cites a bare
"(page 3)" or "(page 10, README.md)", while the project score wants "(Name, page N)" (spec
section 5.7). That is most of the failures. `features` finds all six terms and cites 0
documents; the code, exact-label and procedure cases mostly find the fact and fail on "not
cited". The same question flips between runs only because the model's citation form does:
`session-lifetimes` passed in the first run and failed 3/3 in the repeat, `deploy-and-test`
the same, and at 50 one answer joined two documents in one parenthesis
"(ARCHITECTURE.md, page 3; USER_GUIDE.md, page 1)". The single-run 50 result (1/15) is
therefore not clearly worse than 9; one run each cannot separate the padding from this noise,
and the repeat is what to compare.

Two facts are unfindable today: the README and TESTING command lines sit in fenced code blocks
that extraction skips (spec F4), so the model answers `./startup.sh start` and
`.venv/bin/pytest server/tests` from nearby prose instead of `./startup.sh quickstart` and
`python -m pytest server/tests`. And search is still one cosine sort over every chunk, with no
per-document balance, so the long README and CHAT_WITH_PDF answer `user-guide-pins` (the model
says the user guide has nothing on pins and cites the wrong documents). That last link is
inferred from the answers; the search results themselves weren't inspected.

The web rules fail as expected: there is no `offer_web_search` tool before Task 13, so
`rhel-packages` answers with no tool call and no offer, and `ollama-release` calls
`web_search` itself instead of offering it.

## Layer: code chunks

- Date: 2026-10-18
- Commit: `ddd9fc4` (C2.1 Task 5, code chunks in extraction) plus the uncommitted Task 6 work on top
  (outlines saved, `CHUNKER_VERSION` ending `:code1`, embeddings reused on rebuild), committed with
  this section. No map, no balance; the rules are unchanged.
- Model: `ollama:gemma4:31b-cloud`, thinking off, variant 9, `--repeat 3`, the same scratch database
  as the baseline.
- Web: SearXNG still isn't running; both `live` calls failed with a connection error, as in the baseline.
- Upgrade exercised: the scratch database held the 50 documents indexed under `split1:1200:200`.
  The run's own `run_rebuild` upgraded the nine fixture documents to `split1:1200:200:code1`;
  afterwards the database held 52 code chunks (42 `code`, 10 `code+cont`) and nine `doc_outlines`
  rows, all `headings`. The 41 padding documents (variant 50 only) were not touched. How long the
  upgrade took and how many texts it embedded were not measured here (the unit test
  `test_rebuild_after_chunker_change_embeds_only_new_text` pins "only the new code text").
- Headline: 6/39 passed over three runs, the same as the baseline (6/39); right-document citations
  6/33, the same. What moved is the facts: the three `code` cases, whose commands sit in fenced
  blocks, now find their fact in 9/9 answers (baseline 3/9: `start-command` only), including
  `./startup.sh quickstart` and `python -m pytest server/tests, postgres`, which were unfindable
  before. They still fail on "not cited" (a bare "page 3" instead of "(README.md, page 3)"), which
  this layer doesn't touch.

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
-- run 1
FAIL  overview       features               13.1s rounds=3 tools=search_documents,read_document_pages,read_document_pages  5/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        7.0s rounds=3 tools=search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.2s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.0s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.8s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
PASS  exact-label    session-lifetimes      3.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
FAIL  exact-label    oidc-issuer            2.8s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        4.7s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md); cited the wrong document (CHAT_WITH_PDF.md, README.md)
FAIL  named-doc      deployment-uploads     6.6s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              3.2s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   5.0s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          6.3s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.7s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 2
FAIL  overview       features               6.0s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
PASS  procedure      deploy-and-test        5.6s rounds=3 tools=search_documents,read_document_pages,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok
FAIL  code           start-command          1.5s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.2s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.7s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.9s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.6s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        2.9s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md); cited the wrong document (CHAT_WITH_PDF.md, README.md)
FAIL  named-doc      deployment-uploads     3.9s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              1.7s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.5s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          5.8s rounds=4 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         3.2s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 3
FAIL  overview       features               5.3s rounds=3 tools=search_documents,read_document_pages,read_document_pages  6/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        6.3s rounds=3 tools=search_documents,read_document_pages,read_document_pages,read_document_pages,read_document_pages,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.5s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     2.1s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.2s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
PASS  exact-label    session-lifetimes      1.8s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
FAIL  exact-label    oidc-issuer            2.7s rounds=2 tools=search_documents,search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        2.5s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     4.0s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              2.0s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.5s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          3.8s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         3.4s rounds=2 tools=web_search,web_search  no offer; 2 web calls
tally: overview 0/3, procedure 1/3, code 0/9, exact-label 2/6, named-doc 0/6, balance 3/6, gap-general 0/3, live 0/3
right-document citations 6/33
web calls 6
6/39 passed
```

### What changed against the baseline

Indexing code blocks fixed the findability half of F4: the model now quotes the real commands from
the code chunks instead of guessing from nearby prose, so `quickstart-command` and `test-command`
went from "fact missing" to "fact ok" on every run. The pass count didn't move because those cases,
and most of the others, are decided by the citation form (spec 5.7, a later layer). Per-kind tallies
moved both ways against the baseline's three runs: `procedure` 1/3 (was 0/3), `exact-label` 2/6
(was 0/6), `balance` 3/6 (was 6/6; `next-work` now fails 3/3 on "not cited (DOCS_INDEX.md)" where it
passed 3/3 before). The baseline section shows the same questions flipping on the model's citation
form alone, so I read these as noise rather than an effect of this layer, but I did not inspect the
`next-work` answers or search results to rule out a ranking change from the new code chunks.
`user-guide-pins` still fails on search ranking (the long README and CHAT_WITH_PDF outrank
USER_GUIDE), which the balance layer addresses.

## Project search timing (Task 7: one balanced ranking)

What was measured: `doc_search.search_scope` (one SQL statement over the whole project: exact
meaning scan + word list, fused once, then the per-document balance in Python), 50 searches
after 4 untimed warm-up searches. Only the database work is timed: the query embedding is random
and made locally, so the embedding model's own time is **not** in these numbers. Spec 5.5 target:
p95 under 300 ms.

- Date: 2026-10-18. Code: uncommitted Task 7 work on top of `87e079e`.
- Machine: the CPU dev machine (i9-13980HX, 32 threads) with Postgres 16.15 + pgvector in the
  throwaway container on port 5434 (shared_buffers 128MB, work_mem 4MB, defaults), a scratch
  database, `ANALYZE` run after seeding.
- Data: 50 documents x 200 chunks = 10,000 chunks, random 768-d vectors, 90 Zipf-distributed
  pseudo-words per chunk. Search: `k=10`, `per_doc=2` (`CHAT_SEARCH_PER_DOC`), `depth=50`
  (`CHAT_PROJECT_FUSION_DEPTH`).

```sh
.venv/bin/python scripts/bench_project_search.py \
  --database-url postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_bench \
  --docs 50 --chunks 200
```

Result (milliseconds; "all" is the headline, the rest is by query shape, 12-13 searches each, so
a shape's p95 is close to its maximum):

| query shape | n | p50 | p95 | what it exercises |
|---|---|---|---|---|
| **all** | 50 | **48.1** | **106.8** | the four shapes below, in turn |
| exact | 13 | 47.0 | 51.9 | three words one chunk has: the all-words list finds it |
| partial | 13 | 50.4 | 54.1 | the same plus one word nobody has: the half-the-words list runs |
| partial-common | 12 | 106.1 | 107.3 | two of the commonest words plus an absent one: nearly every chunk is a candidate (worst case) |
| meaning | 12 | 23.8 | 44.5 | no text: the meaning list alone |

p95 106.8 ms against the 300 ms target: **met**, with about 2.8x headroom.

Where the time goes (EXPLAIN ANALYZE, partial-common, 98 ms): the exact meaning scan is about 30
ms (10,000 vectors read from the MATERIALIZED CTE and sorted by distance); the partial word list
is about 65 ms, almost all of it counting matched words for the ~10,000 candidate chunks and
ranking them with `ts_rank_cd`. A normal query touches far fewer candidates (partial: 50 ms
total).

A second run with the same project inside a larger library (250 documents, 50,000 chunks, the
search scoped to 50 of them) gave the same picture (p50 / p95: exact 25.5 / 46.1, partial 49.7 /
54.1, partial-common 105.0 / 107.3, meaning 22.7 / 47.7), so the doc-id filter keeps the cost on
the project's chunks, not the library's. Not measured: libraries much larger than 50,000 chunks,
or a query of many common words (the partial list's per-chunk work grows with the number of
distinct words in the query).
