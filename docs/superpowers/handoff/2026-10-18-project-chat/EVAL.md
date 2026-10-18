# Project chat evaluation: baseline and results

What this file is: the scoreboard for C2.1 "project chat". The evaluation asks a chat model
13 questions (15 in the 50-document variant) about a project of documents, through the real
chat turn path scoped to that project, and scores each answer on the fact, the right document
(cited as "(Name, page N)", the form the project score reads), the right page, and the
web-search rules. Every later layer (map, balance, code chunks, rules, web offer) is compared
with the numbers below, so the baseline section is never edited: later runs are appended as
new sections.
Each layer section also carries a table of FACTS FOUND per case (the fact check on its own, apart from the
citation form that decides the pass rate until the citation layer, Task 15).

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
after one untimed warm-up search per query shape. Only the database work is timed: the query
embedding is random and made locally, so the embedding model's own time is **not** in these
numbers. Spec 5.5 target: p95 under 300 ms, here required **for every query shape**, not only
overall.

- Date: 2026-10-18. Code: Task 7 as committed in `0be9067`, plus the review fix round on top of it
  (informative-word partial matching, `word_match`, the long-query benchmark shapes, unprepared
  statements), uncommitted when these numbers were taken.
- Machine: the CPU dev machine (i9-13980HX, 32 threads) with Postgres 16.15 + pgvector in the
  throwaway container on port 5434 (shared_buffers 128MB, work_mem 4MB, defaults), a scratch
  database, `ANALYZE` run after seeding. The machine was not idle (load average 2-5 from other
  work), so treat single digits as noise.
- Data: 50 documents x 200 chunks = 10,000 chunks, random 768-d vectors, 90 Zipf-distributed
  pseudo-words per chunk (so about 14 of the 32 commonest words are in over half the chunks).
  Search: `k=10`, `per_doc=2` (`CHAT_SEARCH_PER_DOC`), `depth=50` (`CHAT_PROJECT_FUSION_DEPTH`).

```sh
.venv/bin/python scripts/bench_project_search.py \
  --database-url postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_bench \
  --docs 50 --chunks 200            # add --vacuum for the steady state, --other-docs N for a bigger library
```

Query shapes (the script alternates them; with 8-9 searches per shape a shape's p95 is close to
its slowest search):

| shape | what it exercises |
|---|---|
| exact | three words one chunk has: the all-words list finds it |
| partial | the same plus one word nobody has: no chunk has all four, so the partial list runs |
| partial-common | two of the commonest words plus an absent one: both are over half the chunks, so nothing is informative and there is no partial list |
| long-mixed | 23 words: 8 of the commonest, 14 mid-frequency and rare, one nobody has |
| long-common | 32 words: the 31 commonest plus one nobody has. The adversarial case (about half are informative but each is in 30-50% of chunks, so every chunk is a candidate) |
| meaning | no text: the meaning list alone |

Result, milliseconds, table just bulk-loaded and not yet vacuumed (the slow state, what the script
measures by default; a plain VACUUM makes it about 15-30% faster, and the unvacuumed plan shows each
GIN lookup costing about 1 ms instead of 0.1, consistent with the index's pending list, which
autovacuum clears within about a minute of a bulk load; I did not verify the mechanism further):

| query shape | n | p50 | p95 |
|---|---|---|---|
| **all** | 50 | **33.4** | **122.5** |
| exact | 9 | 25.8 | 28.1 |
| partial | 9 | 37.8 | 42.7 |
| partial-common | 8 | 28.5 | 31.3 |
| long-mixed | 8 | 58.5 | 65.8 |
| long-common | 8 | 122.2 | 126.6 |
| meaning | 8 | 21.8 | 23.8 |

Steady state (`--vacuum`, 50 searches): overall p50 31.2, p95 104.3; slowest shape long-common
p50 102.1, p95 112.4. Longer runs (150 searches, 25 per shape): just loaded, three runs, overall
p95 128 / 112 / 139 and long-common p95 135 / 120 / 147; vacuumed, overall p95 100, long-common
p95 107. The same project inside a library of 250 documents (50,000 chunks, `--other-docs 200`,
just loaded): overall p95 165.9, long-common p95 172.0, so the document filter keeps the cost on
the project's chunks. **Target met for every shape (p95 under 300 ms) in every run**, with about 2x
headroom for the worst shape. Not measured: scopes much larger than 10,000 chunks (cost grows
about linearly with the scope), queries of more than about 32 distinct words (one lookup each).

What the review round changed in the numbers. The first version of partial matching passed this
benchmark at 4-word queries (p95 107 ms) but a 32-word query took 360-650 ms; the causes, in order
of size:

1. `ts_rank_cd` over a many-term OR query costs 462 ms for 31 terms over 10,000 chunks (15 ms to
   match the same terms). It now only breaks ties among the first 4 x depth candidates by matched
   count.
2. `matched` was worked out twice per candidate chunk (filter and sort). Now once (LATERAL).
3. Only informative words count (at most half of the scope's chunks have them; the 12 rarest), so
   stop words neither inflate the work nor the bar. Words clearly in over three fifths of a
   1,000-chunk sample skip their index lookup (a lookup for a stop word touches most of the scope).
4. psycopg prepares a statement after 5 runs and PostgreSQL then uses a generic plan that cannot
   see how many documents are in scope; that plan was 40-100% slower on this statement, in this
   benchmark and so in the app. The search now runs unprepared (`prepare=False`, about 1 ms of
   planning), which also speeds up the single-document and short-query paths (exact: 47 to 26 ms).

## Layer: one ranking + balance

- Date: 2026-10-18
- Commit: `87eaa93` (C2.1 Task 7) plus the uncommitted Task 8 work on top: `search_documents` and the
  prefetch call `search_scope` (one ranking over the project, 2 passages per document, partial word
  matches counting a tenth in the fusion (`PARTIAL_WEIGHT = 0.1`), a `documents` filter); the cosine
  floor is skipped only by all-words matches; a result's `documents` listing names only the documents
  its passages refer to. No map; the rules are unchanged.
- Model: `ollama:gemma4:31b-cloud`, thinking off, variant 9, `--repeat 3`, the same scratch database
  as the earlier layers.
- Web: SearXNG still isn't running; both `live` calls failed with a connection error, as before.
- Earlier runs of this layer: partial matches at full weight passed 3/39 (right-document citations 3/33),
  with `next-work` 0/3; at weight 0.5, 2/39 (4/33), `next-work` still 0/3 (the displacing rows were also
  in the meaning list, where a half weight is still worth more than the whole gap between meaning ranks
  1 and 20). This section is the run at 0.1.
- Headline: **2/39 passed** (code chunks layer: 6/39), right-document citations **3/33** (6/33), exit
  status 1. The pass count is decided by the citation form, which flips between runs and is not fixed
  until Task 15; read the facts table instead.

### Facts found per case (the answer's fact, apart from citation form)

Over the three runs; rounds per run in brackets. Fact = the strings the case looks for (for `features`,
how many of its 6 term groups appear, 4 needed). `ollama-release` has no fact (it checks the web offer).

| case | baseline | code chunks | **this run** |
|---|---|---|---|
| features | 6,6,6 of 6 terms [3,3,3] | 5,6,6 of 6 terms [3,3,3] | **3,4,3 of 6 terms [2,2,2]** |
| deploy-and-test | 3/3 [2,3,2] | 3/3 [3,3,3] | **3/3 [2,2,2]** |
| start-command | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| quickstart-command | 0/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [2,2,2]** |
| test-command | 0/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| session-lifetimes | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| oidc-issuer | 3/3 [2,2,2] | 3/3 [2,2,2] | **0/3 [2,2,2]** |
| user-guide-pins | 0/3 [5,3,3] | 0/3 [1,1,1] | **0/3 [1,1,1]** |
| deployment-uploads | 2/3 [3,3,2] | 3/3 [3,3,3] | **3/3 [4,3,3]** |
| next-work | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| changelog-unreleased | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| rhel-packages | 3/3 [3,3,3] | 3/3 [3,4,3] | **3/3 [0,2,2]** |
| ollama-release | no fact to check | no fact to check | **no fact to check** |

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
-- run 1
FAIL  overview       features               5.4s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown, admin|project|projects; 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        5.3s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.3s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     2.7s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.0s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.8s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            3.2s rounds=2 tools=search_documents,search_documents  fact missing (https://auth.example.com/realms/natural-reader, http://localhost:18080/realms/natural-reader); not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        2.3s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     6.0s rounds=4 tools=search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              1.7s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.9s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          0.9s rounds=0 tools=-  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         3.2s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 2
FAIL  overview       features               5.4s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        5.8s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.5s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.7s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.9s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      3.5s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            3.7s rounds=2 tools=search_documents,search_documents  fact missing (https://auth.example.com/realms/natural-reader, http://localhost:18080/realms/natural-reader); documents ok; pages ok
FAIL  named-doc      user-guide-pins        3.0s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     3.8s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              1.6s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.4s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          3.9s rounds=2 tools=search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.1s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 3
FAIL  overview       features               6.8s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown, admin|project|projects; 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        5.4s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.8s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     9.0s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.2s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.0s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            3.2s rounds=2 tools=search_documents,search_documents  fact missing (https://auth.example.com/realms/natural-reader, http://localhost:18080/realms/natural-reader); not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        5.7s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     4.2s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              1.7s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
FAIL  balance        changelog-unreleased   1.7s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
FAIL  gap-general    rhel-packages          3.4s rounds=2 tools=search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.4s rounds=2 tools=web_search,web_search  no offer; 2 web calls
tally: overview 0/3, procedure 0/3, code 0/9, exact-label 0/6, named-doc 0/6, balance 2/6, gap-general 0/3, live 0/3
right-document citations 3/33
web calls 6
2/39 passed
```

### What changed against the previous layer (code chunks)

- **`next-work` recovered**: 3/3 on the fact in one search round, as before this layer (0/3 at full weight and
  at 0.5). Probing `search_scope` on this database, DOCS_INDEX is back at 4th for "next piece of work"
  and "what is the next piece of work"; for "where to find the next piece of work" it is still missing
  at every weight, so this is a gain on the phrasings tried, not on every phrasing. (The model's own
  queries were not captured.)
- **`oidc-issuer` did not recover**: 0/3 on the fact (3/3 before this layer; 2/3 at full weight, 0/3 at 0.5
  and at 0.1). The model searches twice and answers without the two URLs. Probing `search_scope` with
  plausible queries: the question as written finds the right chunk (DEPLOYMENT p2, the table holding both
  values) first, but short queries such as "OIDC_ISSUER" and "OIDC_ISSUER production" do not put it in
  the top 5, with or without the 2-per-document cap, so the balance is not the cause. One contributing
  fact: extraction joins the table's cells without a separator ("OIDC_ISSUERhttp://localhost:18080/..."),
  and the chunk's text index has no `oidc_issuer` word (checked in the database), so an exact-word
  search for `OIDC_ISSUER` cannot reach it and only meaning can. I did not capture the model's own two
  queries, so the cause of the 0/3 is not established. Follow-up, not done here: keep table cells apart
  in extraction.
- `deploy-and-test` stays recovered (3/3, 2 rounds). `features` is unchanged from the first run of this
  layer: 3-4 of 6 terms with 2 rounds instead of 3 (cause not found). `user-guide-pins` is 0/3 on the fact
  as in every layer (USER_GUIDE mentions pins in one line).
- Also from this layer: `doc_search.search_scope` looks at a query's first 64 distinct words only: on
  the 50 x 200 chunk benchmark data (vacuumed) a 200-word query took 129.6 ms p50 before and 65.5 ms
  after; 32- and 64-word queries are unchanged. Timed before the fusion weight, which changes only a
  constant in the fusion's sum.

## Layer: table cells

- Date: 2026-10-18
- Commit: the uncommitted Task 8b work on top of `9ff6d17`: in a Markdown table the chunk text puts a space
  after every cell and a newline after every row (so, once collapsed, one space between any two cells), where
  it used to glue them ("OIDC_ISSUERhttp://localhost:18080/..."). Page packing still counts sentences on
  the glued text (the reader's), so no page moves. `CHUNKER_VERSION` is now `split1:1200:200:code1:cells1`.
  Tables inside other blocks (a list item) are spaced the same way; the paragraph before such a table is not
  separated from it (mdast glues a list item's paragraphs; not touched).
- Model: `ollama:gemma4:31b-cloud`, thinking off, variant 9, `--repeat 3`, the same scratch database.
- Web: SearXNG still isn't running; both `live` calls failed with a connection error, as before.
- Upgrade exercised: the nine fixture documents went from `...:code1` to `...:code1:cells1` in the run's own
  `run_rebuild`; the 41 padding documents (variant 50 only) were not touched. Embeddings of text that did not
  change were reused; the table chunks (and the chunks next to them whose cut moved) were embedded again.
  How many, and how long it took, were not measured.
- Runs: three sets, each `--repeat 3`. Two with the new extraction (the second, reported below, also logged
  the model's search queries; the two agree on every fact) and a control with the OLD extraction on the same day
  (the nine documents were rebuilt back to `...:code1`, then forward again), to separate the layer from the
  model's day-to-day drift.
- Headline: **1/39 passed**, right-document citations **1/33** (control with the old extraction, same day:
  4/39 and 4/33; previous layer's section: 2/39 and 3/33). As in every layer the pass count is decided by
  the citation form and flips between runs; read the facts table. Exit status 1.

### Facts found per case (the answer's fact, apart from citation form)

Over the three runs; rounds per run in brackets. Fact = the strings the case looks for (for `features`, how
many of its 6 term groups appear, 4 needed). `ollama-release` has no fact (it checks the web offer).
"control" = the old extraction (cells glued), re-run today; the other columns are earlier sections.

| case | baseline | code chunks | one ranking + balance | control, today | **this run** |
|---|---|---|---|---|---|
| features | 6,6,6 of 6 terms [3,3,3] | 5,6,6 of 6 terms [3,3,3] | 3,4,3 of 6 terms [2,2,2] | 4,3,3 of 6 terms [2,2,2] | **3,4,4 of 6 terms [2,2,2]** |
| deploy-and-test | 3/3 [2,3,2] | 3/3 [3,3,3] | 3/3 [2,2,2] | 3/3 [2,2,2] | **3/3 [2,2,2]** |
| start-command | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| quickstart-command | 0/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [2,2,2] | 3/3 [2,2,2] | **3/3 [2,2,2]** |
| test-command | 0/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| session-lifetimes | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| oidc-issuer | 3/3 [2,2,2] | 3/3 [2,2,2] | 0/3 [2,2,2] | 1/3 [2,2,3] | **3/3 [1,1,1]** |
| user-guide-pins | 0/3 [5,3,3] | 0/3 [1,1,1] | 0/3 [1,1,1] | 0/3 [1,1,1] | **0/3 [1,1,1]** |
| deployment-uploads | 2/3 [3,3,2] | 3/3 [3,3,3] | 3/3 [4,3,3] | 3/3 [3,3,3] | **3/3 [3,3,3]** |
| next-work | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **0/3 [2,2,2]** |
| changelog-unreleased | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** |
| rhel-packages | 3/3 [3,3,3] | 3/3 [3,4,3] | 3/3 [0,2,2] | 3/3 [3,0,0] | **3/3 [2,3,0]** |
| ollama-release | no fact to check | no fact to check | no fact to check | no fact to check | **no fact to check** |

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
-- run 1
FAIL  overview       features               4.0s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown, admin|project|projects; 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        4.8s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.6s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.0s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.6s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      1.6s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            9.7s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        2.9s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     3.8s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              2.6s rounds=2 tools=search_documents,read_document_pages  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.8s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          3.2s rounds=2 tools=search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.8s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 2
FAIL  overview       features               5.7s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        7.3s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.7s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.5s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           3.0s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.0s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            1.8s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        3.1s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     4.5s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              3.9s rounds=2 tools=search_documents,read_document_pages  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
FAIL  balance        changelog-unreleased   1.7s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
FAIL  gap-general    rhel-packages          4.6s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         3.1s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 3
FAIL  overview       features               5.8s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        6.0s rounds=2 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.4s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.7s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           3.2s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.5s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.4s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        3.5s rounds=1 tools=search_documents  fact missing (each message|every message); not cited (USER_GUIDE.md); cited the wrong document (CHAT_WITH_PDF.md, README.md)
FAIL  named-doc      deployment-uploads     3.9s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              3.9s rounds=2 tools=search_documents,read_document_pages  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
FAIL  balance        changelog-unreleased   1.6s rounds=1 tools=search_documents  fact ok; not cited (DOCS_INDEX.md)
FAIL  gap-general    rhel-packages          0.9s rounds=0 tools=-  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.3s rounds=2 tools=web_search,web_search  no offer; 2 web calls
tally: overview 0/3, procedure 0/3, code 0/9, exact-label 0/6, named-doc 0/6, balance 1/6, gap-general 0/3, live 0/3
right-document citations 1/33
web calls 6
1/39 passed
```

### What changed against the control (same day, old extraction)

- **`oidc-issuer` recovered on the fact**: 3/3 in ONE search round each (control 1/3 in 2-3 rounds; the previous
  layer's section 0/3). The model's query is the bare variable name `OIDC_ISSUER` (the control's second
  query, when the first failed, was a variant such as `OIDC_ISSUER value`). Probing `search_scope` with that query on this database: before, the table chunk
  (DEPLOYMENT p2) was not among the top 5 at all (two prose chunks matched the word, nothing else); now it is
  the first result with `word_match` 'all' (the generated text index finds `oidc_issuer` in 4 DEPLOYMENT
  chunks), and with "OIDC_ISSUER production local dev" it is first at score 0.759 instead of absent. This is
  what the layer was for, and it did what it was meant to.
- **`session-lifetimes` did not change**: 3/3 on the fact in one round, with and without the layer. Its
  variables (`SESSION_TTL_HOURS`, `SESSION_IDLE_HOURS`) sit in prose in ARCHITECTURE p3, not in a table, so
  there was nothing for this layer to fix; the case failed before and fails now only on the citation form
  ("not cited (ARCHITECTURE.md)", 2 of 3 runs; it passed once in the control).
- **`next-work` regressed on the fact**: 0/3 in two rounds each (search, then a page read), where the control
  and every earlier layer had 3/3 in one. Reproduced in both new sets of runs, and the control, with the old
  extraction, was 3/3 the same day, so it is the layer and not the model's day. The model's query is `next
  piece of work`. The answer ("HANDOVER") is in a row of DOCS_INDEX's big table (12: `../HANDOVER.md Session
  continuity ... what's next`) and in a "Quick routing" line; that table is chunked into a few parts, and the
  extra spaces moved the cut points, so the part holding row 12 now starts at a different place and
  its text differs from the glued text. Its cosine similarity to the query dropped from 0.638 to 0.595, which
  took it from 4th (in the search result) to below 12th in the meaning ranking, out of the 5 results the model sees. The "Quick
  routing" line (similarity 0.49) never made it either way. So the earlier 3/3 was resting on how a glued
  table happened to embed, and the earlier layer's note ("a gain on the phrasings tried, not on every
  phrasing") applies in the other direction now. I did not try to repair this here: it needs either a table
  chunking that keeps a row's meaning together or a better ranking for short question-like queries, both
  outside this task.
- Everything else is the same within the noise seen in earlier layers (`features` 3-4 of 6 terms; the
  `rhel-packages` rounds jump between 0 and 3; `user-guide-pins` is 0/3 on the fact in every layer).

## Layer: map

- Date: 2026-10-18
- Commit: the uncommitted Task 9 work on top of `f5cf7a2`: `server/chat/project_map.py` (`build_map`), the map in the
  system message between the rules and the pins, `document_outline` (offered only when the map was shortened),
  `CHAT_PROJECT_MAP_CHARS` (6000), and the data rule naming `<project_documents>`. The rules' procedure is
  unchanged (it still says "the open document"; Task 14), so the citation form is unchanged too.
- Model: `ollama:gemma4:31b-cloud`, thinking off, `--repeat 3` for variant 9 and `--repeat 1` for variant 50, the same
  scratch database. The 41 padding documents were upgraded to the current chunker by the run's own `run_rebuild`
  (their first use since the code-chunks layer); every one of the 50 has a stored outline.
- Web: SearXNG still isn't running; the `live` calls failed with a connection error, as before.
- Two sets of variant 9: set 1 is the plain run (the reference below); set 2 was run afterwards through a scratch
  wrapper that prints each case's tool calls WITH their arguments (nothing else differs). The variant 50 run was also
  made with the wrapper.
- Headline: variant 9 **4/39 passed in both sets** (table cells layer: 1/39), right-document citations **4/33 in both**
  (1/33); variant 50 passed 1/15 (right-document citations 1/13; baseline 1/15, 1/13). Exit status 1. As in every
  layer the pass count is decided by the citation form (Task 15), so read the facts table.

### What the map looked like

Measured on the eval database with `build_map` at the default budget (6000 characters; sizes are the whole block,
tags and note included):

| variant | documents | full map | step the budget landed on | shortened map | `document_outline` offered |
|---|---|---|---|---|---|
| 9 | 9 | 6998 | drop level 3 (levels 1-2 kept) | 3119 | yes |
| 50 | 50 | 13805 | drop level 2 (level 1 only) | 4190 | yes |

So the 50-document map **was shortened, and so was the 9-document one** (its full outlines are 1000 characters over
the budget). In variant 50 the shortening went as far as level 1: the nine fixture documents show only their title
("Neural Reader p1", "Docs index — read in this order p1"), because each is one `#` title with its sections at level
2; the 41 padding documents each show their title. The other steps, for variant 50, come to about 9,900 characters for levels 1-2 (over
budget), about 4,190 for level 1, the same again for "first 8 entries" (nothing to cut when a document has one
level-1 heading), and about 2,150 for names and pages only.

**`document_outline` was never called**: not in any of the six variant-9 runs (78 turns), nor in the variant 50 run
(15 turns), although it was offered in all of them (the maps were shortened).

### Facts found per case (the answer's fact, apart from citation form)

Over the three runs of a set; rounds per run in brackets. Fact = the strings the case looks for (for `features`, how
many of its 6 term groups appear, 4 needed). `ollama-release` has no fact (it checks the web offer). The columns
before "map" are the earlier sections' numbers.

| case | baseline | code chunks | one ranking + balance | table cells | **map, set 1** | **map, set 2** |
|---|---|---|---|---|---|---|
| features | 6,6,6 of 6 terms [3,3,3] | 5,6,6 of 6 terms [3,3,3] | 3,4,3 of 6 terms [2,2,2] | 3,4,4 of 6 terms [2,2,2] | **3,3,4 of 6 terms [2,2,2]** | **4,3,4 of 6 terms [2,2,2]** |
| deploy-and-test | 3/3 [2,3,2] | 3/3 [3,3,3] | 3/3 [2,2,2] | 3/3 [2,2,2] | **3/3 [2,2,2]** | **3/3 [2,2,2]** |
| start-command | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| quickstart-command | 0/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [2,2,2] | 3/3 [2,2,2] | **3/3 [2,2,2]** | **3/3 [2,2,2]** |
| test-command | 0/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| session-lifetimes | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| oidc-issuer | 3/3 [2,2,2] | 3/3 [2,2,2] | 0/3 [2,2,2] | 3/3 [1,1,1] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| user-guide-pins | 0/3 [5,3,3] | 0/3 [1,1,1] | 0/3 [1,1,1] | 0/3 [1,1,1] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| deployment-uploads | 2/3 [3,3,2] | 3/3 [3,3,3] | 3/3 [4,3,3] | 3/3 [3,3,3] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| next-work | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 0/3 [2,2,2] | **1/3 [1,1,1]** | **3/3 [1,1,1]** |
| changelog-unreleased | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | 3/3 [1,1,1] | **3/3 [1,1,1]** | **3/3 [1,1,1]** |
| rhel-packages | 3/3 [3,3,3] | 3/3 [3,4,3] | 3/3 [0,2,2] | 3/3 [2,3,0] | **3/3 [3,2,3]** | **3/3 [1,2,1]** |
| ollama-release | no fact to check | no fact to check | no fact to check | no fact to check | **no fact to check** | **no fact to check** |

Variant 50, one run (facts): every case's fact found except `next-work` (missing) and `features` (3 of 6 terms, 4 needed;
5 of 6 in its `features-50` repeat); `user-guide-pins` and `deployment-uploads` found in one round each. The baseline's
variant 50 run missed the fact in five cases (deploy-and-test, quickstart-command, test-command,
user-guide-pins, deployment-uploads).

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
-- run 1
FAIL  overview       features               17.7s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown, admin|project|projects; 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        7.6s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.2s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     4.3s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           4.3s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      4.3s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            3.0s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        2.2s rounds=1 tools=search_documents  fact ok; not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     2.8s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              3.2s rounds=1 tools=search_documents  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   2.4s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          8.6s rounds=3 tools=search_documents,search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.2s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 2
FAIL  overview       features               4.4s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown, admin|project|projects; 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        6.6s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          3.3s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.2s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.4s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.1s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            4.3s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        1.9s rounds=1 tools=search_documents  fact ok; not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     2.3s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              3.3s rounds=1 tools=search_documents  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.5s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          4.0s rounds=2 tools=search_documents,search_documents  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.5s rounds=2 tools=web_search,web_search  no offer; 2 web calls
-- run 3
FAIL  overview       features               4.4s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        5.7s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.7s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     2.6s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.1s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.0s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            1.9s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        1.8s rounds=1 tools=search_documents  fact ok; not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     1.7s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md)
PASS  balance        next-work              2.1s rounds=1 tools=search_documents,search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.7s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          6.4s rounds=3 tools=search_documents,search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         1.5s rounds=1 tools=web_search  no offer; 1 web calls
tally: overview 0/3, procedure 0/3, code 0/9, exact-label 0/6, named-doc 0/6, balance 4/6, gap-general 0/3, live 0/3
right-document citations 4/33
web calls 5
4/39 passed
```

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 50)
FAIL  overview       features               4.7s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown, admin|project|projects; 1 documents cited (need 3)
FAIL  procedure      deploy-and-test        6.2s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.2s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     3.7s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           2.0s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.1s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.3s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        1.9s rounds=1 tools=search_documents  fact ok; not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     2.3s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              2.9s rounds=2 tools=search_documents,read_document_pages  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.8s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          4.6s rounds=3 tools=search_documents,read_document_pages,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         2.6s rounds=2 tools=web_search,web_search  no offer; 2 web calls
FAIL  map-shortened  features-50            4.7s rounds=2 tools=search_documents,read_document_pages  5/6 terms (need 4); 0 documents cited (need 3)
FAIL  map-shortened  start-command-50       1.9s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
right-document citations 1/13
web calls 2
1/15 passed
```

Tool calls with their arguments (set 2, then the variant 50 run), for the cases the map is meant to move. `document`
numbers are the map's refs; in variant 9 they are 3 DEPLOYMENT.md, 4 DOCS_INDEX.md, 7 README.md, 8 TESTING.md,
9 USER_GUIDE.md; in variant 50 the padding notes are interleaved by name, so README.md is 40, DEPLOYMENT.md 24,
DOCS_INDEX.md 25, USER_GUIDE.md 46, CHAT_WITH_PDF.md 19.

```text
-- variant 9, set 2
features: search_documents({"query": "features"}) | read_document_pages({"document": 7, "first_page": 1})
user-guide-pins: search_documents({"documents": [9], "query": "pins"})
deployment-uploads: search_documents({"documents": [3], "query": "larger PDF uploads fail"})
next-work: search_documents({"query": "next piece of work"}) | search_documents({"query": "what's next"})
features: search_documents({"query": "features"}) | read_document_pages({"document": 7, "first_page": 1})
user-guide-pins: search_documents({"documents": [9], "query": "pins"})
deployment-uploads: search_documents({"documents": [3], "query": "larger PDF uploads fail"})
next-work: search_documents({"query": "next piece of work"}) | search_documents({"query": "what's next"})
features: search_documents({"query": "features"}) | read_document_pages({"document": 7, "first_page": 1})
user-guide-pins: search_documents({"documents": [9], "query": "pins"})
deployment-uploads: search_documents({"documents": [3], "query": "larger PDF uploads fail"})
next-work: search_documents({"query": "next piece of work"}) | search_documents({"query": "What's next"})
-- variant 50
features: search_documents({"query": "features"}) | read_document_pages({"document": 40, "first_page": 1})
user-guide-pins: search_documents({"documents": [46], "query": "pins"})
deployment-uploads: search_documents({"documents": [24], "query": "larger PDF uploads fail"})
next-work: search_documents({"query": "next piece of work"}) | read_document_pages({"document": 19, "first_page": 8})
features-50: search_documents({"query": "features"}) | read_document_pages({"document": 40, "first_page": 1})
```

### What changed against the previous layer (table cells)

- **`user-guide-pins` found, for the first time**: 3/3 in both sets, in ONE round (0/3 in every earlier layer). The
  model reads "USER_GUIDE.md" off the map and searches only that document: `search_documents({"documents": [9],
  "query": "pins"})`. Before, the long README and CHAT_WITH_PDF crowded it out of a project-wide search. The same in
  variant 50 (`documents: [46]`). This is what the map is for: choosing the document by name before searching.
- **`deployment-uploads`** went from 3 rounds to 1 for the same reason (`documents: [3]`, "larger PDF uploads fail").
- **`next-work` came back only partly**: fact found 1/3 in set 1 and 3/3 in set 2 (it was 0/3 in the previous layer
  and 3/3 before it), and not in variant 50 (the model read CHAT_WITH_PDF p8 instead). It did **not** reach DOCS_INDEX
  by its outline: DOCS_INDEX's whole outline is one heading ("Docs index — read in this order"), and no logged
  run ever restricted a search to it (`documents: [4]`). What happened in set 2 (all three runs, queries logged) is a
  retry: the model searched "next piece of work", then "what's next" (or "What's next"). Probing `search_scope` on this
  database with those two queries (project-wide, k=5, 2 per document): "next piece of work" puts a DOCS_INDEX
  chunk 5th, but not the one with the HANDOVER row; "what's next" puts the row that holds HANDOVER 3rd (an exact-word
  match, `word_match` 'all'). The retry phrase is the heading text the map shows for CHAT_WITH_PDF ("11. What's
  next p8") and LIBRARY ("What's next (out of scope here) p6"), which is the likely source of the words (inferred; the
  model's reasoning isn't captured). In set 1 the two runs that failed made ONE search (tools column) and the run
  that passed made two; its queries weren't logged. So the map helped by giving the model words to retry with, and the
  retry's exact-word match did the rest. The case remains noise-sensitive (4 of 6 runs), and its citation still
  fails more often than not ("not cited (DOCS_INDEX.md)": the answer names the file in prose or says "(page 1)").
- **`oidc-issuer`, `session-lifetimes`, the `code` cases, `changelog-unreleased`**: unchanged (3/3, one or two
  rounds). `rhel-packages` still finds its fact (the documents' requirements) while failing "didn't say the documents
  don't cover it" in most runs; that is the rules (Task 14) and the web offer (Task 13). `features` still lands on 3-4
  of 6 term groups in two rounds, as in the last three layers.
- **The pass count moved from 1/39 to 4/39** in both sets: `changelog-unreleased` passes 3/3 and `next-work` once.
  `changelog-unreleased` went 3/3, 3/3, 3/3, 1/3 over the earlier layers on the citation form alone, so I do not
  credit the map for it; `next-work`'s pass is the fact being found AND the answer naming DOCS_INDEX. Every other case that
  now finds its fact still fails on "not cited (Name.md)" or the page, which Tasks 14-15 address.
- **A weakness the 50-document run shows**: when the budget forces the shortening to level 1, the map tells the model
  nothing about sections, because Markdown documents typically have ONE level-1 heading (their title). The ladder's
  "first 8 entries" step comes after "level 1 only", so it has nothing left to cut on such documents. The model
  still did well because document NAMES carry most of the routing in this fixture; a project whose documents are
  named alike would not. `document_outline` is the way back to the sections, and the model did not use it here.
  Whether to put "first 8 entries" before "drop level 2" is a decision for the spec (5.2 fixes the order as built); not
  changed here.

## Layer: map (shortening order)

- Date: 2026-10-18
- Change: the map's shortening order is now: drop level 3, then cap each outline at its first 8 entries while level 2
  is still kept, then drop level 2 as well, then names and pages only. Before: drop level 3, drop level 2, cap at 8,
  names only. Why: a Markdown outline has ONE level-1 heading (the title) and its sections at level 2, so dropping
  level 2 first left titles only and gave the cap nothing to cut (seen in the 50-document run of the section above).
  `STEPS` in `server/chat/project_map.py`; regression test `test_a_cap_comes_before_dropping_level_2`.
- Model: `ollama:gemma4:31b-cloud`, thinking off, variant 9, `--repeat 1`, the same scratch database. Made through the
  same scratch wrapper as set 2 above (prints each case's tool arguments; changes nothing else). Variant 50 was not run.
- **The variant 9 map did not change.** It still stops at the second step (drop level 3, levels 1-2 kept): 3119
  characters, with the note; the full map is 6998 and the budget 6000. The new third step (levels 1-2 capped at 8)
  would be about 2,550 and the fourth (level 1, capped) about 880, but the second already fits, so the text the model
  saw is byte-for-byte the map of the section above. This run is therefore one more sample of the same layer, not a test of
  the new order.
- For variant 50 (computed on the eval database, not run): about 13,800 characters in full, about 9,900 without level
  3, about 9,350 capped at 8 entries with level 2 kept, about 4,190 with level 1 only (capped), about 2,150 names and
  pages only. At 6000 it still lands on level 1, so the model would still see titles only; the new order helps a
  project only when the capped level-2 map fits, here from a budget of roughly 9,400 characters. Most of the 50
  documents are the 41 padding notes, each of which has only 4 headings, so the cap cannot reduce them.
- Headline: 1/13 passed (the two earlier sets of three runs passed 4/39 each), right-document citations 1/11. The citation form still
  decides the pass count.

### Facts found per case

One run. The columns before this one are the previous section's two sets of three runs; rounds in brackets.

| case | map, set 1 | map, set 2 | **this run (new order)** |
|---|---|---|---|
| features | 3,3,4 of 6 terms [2,2,2] | 4,3,4 of 6 terms [2,2,2] | **4 of 6 terms [2]** |
| deploy-and-test | 3/3 [2,2,2] | 3/3 [2,2,2] | **1/1 [2]** |
| start-command | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| quickstart-command | 3/3 [2,2,2] | 3/3 [2,2,2] | **1/1 [2]** |
| test-command | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| session-lifetimes | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| oidc-issuer | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| user-guide-pins | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| deployment-uploads | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| next-work | 1/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| changelog-unreleased | 3/3 [1,1,1] | 3/3 [1,1,1] | **1/1 [1]** |
| rhel-packages | 3/3 [3,2,3] | 3/3 [1,2,1] | **1/1 [3]** |
| ollama-release | no fact to check | no fact to check | **no fact to check** |

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
FAIL  overview       features               5.6s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        6.3s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          2.4s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           quickstart-command     2.5s rounds=2 tools=search_documents,read_document_pages  fact ok; not cited (README.md); page missing (want README.md p. 3)
FAIL  code           test-command           1.6s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      3.0s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            1.7s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        1.4s rounds=1 tools=search_documents  fact ok; not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     2.5s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              2.3s rounds=1 tools=search_documents,search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   1.6s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          5.1s rounds=3 tools=search_documents,search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
FAIL  live           ollama-release         7.6s rounds=2 tools=web_search,web_search  no offer; 2 web calls
right-document citations 1/11
web calls 2
1/13 passed
```

- **`next-work` did not change in kind**: fact found, in one round, with the same two searches as in set 2
  ("next piece of work", then "what's next"; the model's own arguments, logged), and failing on the citation ("not cited
  (DOCS_INDEX.md)"; the answer itself wasn't printed in this run). Over all seven runs of this map it has been found 5 times
  (1/3, 3/3, 1/1). The map it saw is the same as before, so this is noise around the earlier result, not an effect of the
  new order.
- `user-guide-pins` again 1 round with `documents: [9]`; every other fact as in the earlier sets.

## Layer: web

- Date: 2026-10-18
- Change (C2.1 Task 13, uncommitted at the time of the run): web search asks first. The default mode is `ask`: the model's
  only web tool is `offer_web_search`, and a call ends the turn with an offer. The user's yes comes back as the next turn's
  `context.web`, and the app runs that search itself before the model's first step. (As run here, `web_search` and
  `read_web_pages` were offered to the model for that turn. Fix round 1 narrowed it: in an approved turn the model gets
  `read_web_pages` and `offer_web_search` only, never `web_search`, because the yes was for the one search the user saw.
  The run below was not repeated; the model's calls in it were the app's search and one `read_web_pages`, which the
  narrowed set still allows.) `auto` offers `web_search` and `read_web_pages` directly and `off` offers none. The rules text in `prompt.py` is
  unchanged (Task 14 writes the offer sentences), so in `ask` mode the model is still told "say you can't look it up here"
  for live questions: read the offer numbers below with that in mind.
- Model: `ollama:gemma4:31b-cloud`, thinking off, variant 9, `--repeat 1`, the scratch database `natural_reader_eval`.
  Command: `--variant 9 --repeat 1 --web-fixture` (plus `--show-answers`). The reader used the default `llama3.2:3b` (no
  `READER_MODEL` or `SUMMARIZE_MODEL` is set here); the fake web served the pages in this process, no network.
- New: `--web-fixture`. It replaces `searxng_search` and the page fetch in the eval's own process with files from
  `server/evals/fixtures/web/` (HTML that trafilatura extracts, like a fetched page), and asks the `web-read` case too.
  Nothing is served over HTTP and the product code has no bypass of the SSRF guard. The fixture is a made-up product
  (Marrowdeck): the answer, package `marrowdeck-srv` at minimum version `4.18.3`, is on the current guide's page at
  character 7,545 of 8,738 extracted (past the 6,000 where the old fetch cut a page). The archived 4.17 guide is listed
  first in every search and gives `4.17.3` in its snippet as well: the near-miss.
- `web-read` runs as a user would: turn 1 is the question (an offer is expected, and recorded); turn 2 is "Search the web
  for: <the offered query>" with `context.web` set to it (the question itself when no offer came); turn 2 is scored on the
  facts alone (`marrowdeck-srv` and `4.18.3` found, `4.17.3` not given).
- Headline: 3/14 passed in this one run (the previous layer passed 1/13 in its one run), right-document citations 2/11.
  The citation form still decides most of the failures (see the table). The new case passed.

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
FAIL  overview       features               5.9s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 0 documents cited (need 3)
FAIL  procedure      deploy-and-test        7.3s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; not cited (DEPLOYMENT.md, TESTING.md)
FAIL  code           start-command          1.9s rounds=1 tools=search_documents  fact ok; not cited (README.md); page missing (want README.md p. 3)
PASS  code           quickstart-command     4.9s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok
FAIL  code           test-command           2.4s rounds=1 tools=search_documents  fact ok; not cited (TESTING.md); page missing (want TESTING.md p. 1)
FAIL  exact-label    session-lifetimes      2.3s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
FAIL  exact-label    oidc-issuer            2.1s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md); page missing (want DEPLOYMENT.md p. 2)
FAIL  named-doc      user-guide-pins        2.3s rounds=1 tools=search_documents  fact ok; not cited (USER_GUIDE.md)
FAIL  named-doc      deployment-uploads     1.8s rounds=1 tools=search_documents  fact ok; not cited (DEPLOYMENT.md)
FAIL  balance        next-work              2.1s rounds=1 tools=search_documents,search_documents  fact ok; not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   2.1s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          1.7s rounds=2 tools=search_documents,offer_web_search  fact missing (RHEL); didn't say the documents don't cover it; offered the search
FAIL  live           ollama-release         0.7s rounds=0 tools=-  no offer; 0 web calls
PASS  web-read       web-read               29.8s rounds=1 tools=web_search,read_web_pages  no offer; fact ok; near-miss avoided; 2 web calls
right-document citations 2/11
web calls 2
3/14 passed
```

### The web-read case

- **Offered: no.** Turn 1 (the question as typed) ended without an offer, so the `context.web` of turn 2 carried the
  question itself, as the eval is built to do. Turn 1's text isn't captured (only the scored turn's answer is kept), so the
  cause is inferred: the rules still tell a model with no `web_search` to "say you can't look it up here" (see Change),
  and that is what it said for `ollama-release`.
- **Facts found: yes.** The app ran the search first (the fake web was asked once, with the question), the model then
  called `read_web_pages` once, on the current guide only (it fetched `/4.18/install` and not the archived page that was
  listed first), and answered `marrowdeck-srv` at minimum version `4.18.3`.
- **Near-miss avoided: yes.** `4.17.3`, which the first result's snippet gave, was not in the answer.
- Whether the reader returned facts or fell back to its excerpt isn't captured by the eval (the answer carries the figure
  from past the 6,000th character either way): unverified.
- Time 29.8 s for turn 2, which includes the reader call (the default `llama3.2:3b`, on this CPU-only machine); turn 1's
  time isn't counted.

### The question the documents don't answer (`rhel-packages`)

It now **ends with an offer**: tools `search_documents`, `offer_web_search`, and the report's "offered the search". It still
fails the case: the model wrote no text at all in that turn (the answer is empty), so the fact (`RHEL`) and "didn't say the
documents don't cover it" are both missing. That is the rules' job (Task 14): nothing in the prompt yet tells the model to
say what the documents lack before it offers. `ollama-release` (live) did not offer, for the reason in the Change line.

### Facts found per case (the answer's fact, apart from citation form)

One run. "Previous" is the last section's single run (map, shortening order); rounds in brackets.

| case | previous layer | **this run (web)** |
|---|---|---|
| features | 4 of 6 terms [2] | **4 of 6 terms [2]** |
| deploy-and-test | 1/1 [2] | **1/1 [2]** |
| start-command | 1/1 [1] | **1/1 [1]** |
| quickstart-command | 1/1 [2] | **1/1 [2]** |
| test-command | 1/1 [1] | **1/1 [1]** |
| session-lifetimes | 1/1 [1] | **1/1 [1]** |
| oidc-issuer | 1/1 [1] | **1/1 [1]** |
| user-guide-pins | 1/1 [1] | **1/1 [1]** |
| deployment-uploads | 1/1 [1] | **1/1 [1]** |
| next-work | 1/1 [1] | **1/1 [1]** |
| changelog-unreleased | 1/1 [1] | **1/1 [1]** |
| rhel-packages | 1/1 [3] | **0/1 [2] (empty answer, then the offer)** |
| ollama-release | no fact to check | **no fact to check [0] (declined, no offer)** |
| web-read (new) | not asked | **1/1 [1] (offered: no; near-miss avoided)** |

### What changed against the previous layer

- The document cases are unchanged in kind: the same facts found in the same rounds, and the same citation failures.
  `quickstart-command` passed this time (its page is cited), which is noise on one run; nothing in this layer touches it.
- `rhel-packages` lost its fact because the turn now ends in an offer with no text (it found RHEL in the documents'
  requirements before, by answering in prose). That is the cost of the offer ending the turn, until Task 14 tells the
  model to write the gap first.
- `ollama-release` took 0.7 s and zero rounds: with no web tool offered the model declines in one step. The case scores
  "offered and no web call", so it fails until Task 14's rules tell the model to call the offer for live questions.
- The default-mode behaviour itself (the offer ends the turn, the yes runs the app's search, the tools appear for that turn
  only, `auto` and `off`) is covered by `server/tests/test_chat_web_consent.py`, not by this model run.


## Layer: strategy

- Date: 2026-10-19 (the single-document baseline was taken 2026-10-18, before the rewrite).
- Change (C2.1 Task 14, uncommitted at the time of the runs; HEAD `5b3fb6c`): the chat rules are rewritten as one strategy that
  also serves a project chat and the web modes (`server/chat/prompt.py`). New in the text the model reads:
  - a project chat's first line (`The user is asking about the project "…": its n documents are listed in <project_documents>
    below.`, then the document open in the reader by its number), step 2 that sends the model to the map by number and name
    (with `extract_from_documents` and, when the map was shortened, `document_outline`), and step 4 citing `(Name, page N)`;
  - the gap rule: what the documents lack may be said plainly and filled from general knowledge, introduced as not from the
    documents and never with a page (before: "say the document doesn't seem to cover it, rather than filling the gap from
    memory or the web");
  - the web lines by mode: `ask` tells the model to call `offer_web_search` for live information or outside facts, the approved
    turn tells it the results are above and to call `read_web_pages` on the best one to three, `auto` is search then read;
    step 4 adds "(example.com)" for web facts and the offer sentence.
  Tool side: in a project the search's empty result says "No passages about this in the project's documents."; in an approved
  turn `read_web_pages` with nothing to read says the approved search found nothing (it no longer says "Search first.", which
  needs a tool the model doesn't have).
- Scoring changes made for this layer (`server/evals/doc_qa.py`, `project_qa.py`): in `ask` mode a single-document `live` case passes on
  `offer_web_search` with no `web_search` call (auto mode keeps "web_search was called"); the project `gap-general` case passes
  on a written refusal that names the gap, or on an offer that refuses in words or wrote no text, and in either case cites no page
  for RHEL (before: it needed the refusal, the term RHEL and the offer together). Nothing else was loosened. The baseline below was
  scored with the same code, so the two single-document columns are comparable.
- `scripts/eval_doc_qa.py` now has the guards of `eval_project_qa.py`: it refuses a `DATABASE_URL` that is unset or names
  `natural_reader`, and uses its own temporary `DOC_STORAGE_DIR`, removed afterwards (`--storage-dir PATH` keeps another).
- Model: `ollama:gemma4:31b-cloud`, thinking off, `DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_eval`.
  Reader: the default `llama3.2:3b` (no `READER_MODEL` set), fake web from `--web-fixture`.
- How the single-document baseline was taken: **in the main tree, before `prompt.py` was edited** (the guarded script and the new
  scoring in place, the rules exactly as at `5b3fb6c`), `CHAT_WEB_SEARCH=ask`, `--repeat 3`. No worktree was needed.

### Single-document eval, before and after (`CHAT_WEB_SEARCH=ask`, `--repeat 3`)

| case (kind) | before the rewrite | after the rewrite |
|---|---|---|
| single-hop | 3/3, search_documents, 1 round | 3/3, same |
| two-hop | 3/3, 1 round | 3/3, same |
| exact-label | 3/3, 1 round | 3/3, same |
| page-read | 3/3, read_document_pages | 3/3, same |
| absent | 3/3, refused ("does not seem to cover"), no page, no figure | 3/3, same words, no offer, no general knowledge added |
| small-talk | 3/3, no tools | 3/3 |
| general | 3/3 (1969, no page) | 3/3 |
| live | **0/3**: declined ("I can't look up live information"), no tool, no offer | **3/3**: `offer_web_search` in one round each, no text written before it, no `web_search` |
| follow-up | 3/3, no new search | 3/3 |
| **total** | **24/27**, inline citations 12/12 | **27/27**, inline citations 12/12 |

No non-live case dropped (the stop rule did not trigger). Times per case are within noise of each other (1-4 s on the cloud model).

```text
Document QA eval: ollama:gemma4:31b-cloud
-- run 1
PASS  single-hop   1.7s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  two-hop      3.5s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  exact-label  1.9s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  page-read    2.0s rounds=1 tools=read_document_pages  fact ok; pages ok (inline)
PASS  absent       1.9s rounds=2 tools=search_documents,search_documents  refused
PASS  small-talk   0.6s rounds=0 tools=-  no tools, no page
PASS  general      0.5s rounds=0 tools=-  fact ok; no page
FAIL  live         0.6s rounds=0 tools=-  didn't offer the search
PASS  follow-up    0.6s rounds=0 tools=-  fact ok; no new search
-- run 2
PASS  single-hop   1.1s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  two-hop      1.9s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  exact-label  1.2s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  page-read    1.2s rounds=1 tools=read_document_pages  fact ok; pages ok (inline)
PASS  absent       2.5s rounds=2 tools=search_documents,search_documents  refused
PASS  small-talk   0.6s rounds=0 tools=-  no tools, no page
PASS  general      0.6s rounds=0 tools=-  fact ok; no page
FAIL  live         0.6s rounds=0 tools=-  didn't offer the search
PASS  follow-up    0.6s rounds=0 tools=-  fact ok; no new search
-- run 3
PASS  single-hop   1.2s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  two-hop      1.3s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  exact-label  1.2s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  page-read    1.1s rounds=1 tools=read_document_pages  fact ok; pages ok (inline)
PASS  absent       1.8s rounds=2 tools=search_documents,search_documents  refused
PASS  small-talk   0.6s rounds=0 tools=-  no tools, no page
PASS  general      0.5s rounds=0 tools=-  fact ok; no page
FAIL  live         0.6s rounds=0 tools=-  didn't offer the search
PASS  follow-up    0.6s rounds=0 tools=-  fact ok; no new search
tally: single-hop 3/3, two-hop 3/3, exact-label 3/3, page-read 3/3, absent 3/3, small-talk 3/3, general 3/3, live 0/3, follow-up 3/3
inline citations 12/12 (the model's own "(page N)"; chips count for a pass)
24/27 passed
```

```text
Document QA eval: ollama:gemma4:31b-cloud
-- run 1
PASS  single-hop   3.5s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  two-hop      2.2s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  exact-label  2.1s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  page-read    2.1s rounds=1 tools=read_document_pages  fact ok; pages ok (inline)
PASS  absent       2.2s rounds=2 tools=search_documents,search_documents  refused
PASS  small-talk   0.7s rounds=0 tools=-  no tools, no page
PASS  general      1.3s rounds=0 tools=-  fact ok; no page
PASS  live         1.6s rounds=1 tools=offer_web_search  offered the search
PASS  follow-up    2.6s rounds=0 tools=-  fact ok; no new search
-- run 2
PASS  single-hop   1.9s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  two-hop      1.3s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  exact-label  1.9s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  page-read    1.2s rounds=1 tools=read_document_pages  fact ok; pages ok (inline)
PASS  absent       4.4s rounds=2 tools=search_documents,search_documents  refused
PASS  small-talk   0.7s rounds=0 tools=-  no tools, no page
PASS  general      0.6s rounds=0 tools=-  fact ok; no page
PASS  live         0.6s rounds=1 tools=offer_web_search  offered the search
PASS  follow-up    0.6s rounds=0 tools=-  fact ok; no new search
-- run 3
PASS  single-hop   1.3s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  two-hop      2.1s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  exact-label  3.4s rounds=1 tools=search_documents  fact ok; pages ok (inline)
PASS  page-read    1.5s rounds=1 tools=read_document_pages  fact ok; pages ok (inline)
PASS  absent       2.0s rounds=2 tools=search_documents,search_documents  refused
PASS  small-talk   1.6s rounds=0 tools=-  no tools, no page
PASS  general      0.6s rounds=0 tools=-  fact ok; no page
PASS  live         1.5s rounds=1 tools=offer_web_search  offered the search
PASS  follow-up    0.6s rounds=0 tools=-  fact ok; no new search
tally: single-hop 3/3, two-hop 3/3, exact-label 3/3, page-read 3/3, absent 3/3, small-talk 3/3, general 3/3, live 3/3, follow-up 3/3
inline citations 12/12 (the model's own "(page N)"; chips count for a pass)
27/27 passed
```

### Project eval, variant 9, `--repeat 3 --web-fixture`

Two attempts, same command. **The first (37/42 passed) had one turn end in `provider_error`**: `deployment-uploads` in run 2 took
67 s and the cloud provider answered HTTP 502 "connection reset by peer" (a network failure; the report shows ERROR and the exit
status was 2). Since a failed cloud call says nothing about the rules, the command was run again, and the second attempt (below)
had no error. The first attempt's other 41 turns were consistent with it (features 0/3, procedure 3/3, code 9/9, exact-label 5/6,
named-doc 5/6 with the error counted as a miss, balance 6/6, gap-general 3/3, live 3/3, web-read 3/3).

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
-- run 1
FAIL  overview       features               5.6s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 2 documents cited (need 3)
PASS  procedure      deploy-and-test        6.8s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; documents ok
PASS  code           start-command          1.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  code           quickstart-command     2.1s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok
PASS  code           test-command           1.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  exact-label    session-lifetimes      1.7s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  exact-label    oidc-issuer            1.5s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  named-doc      user-guide-pins        1.4s rounds=1 tools=search_documents  fact ok; documents ok
PASS  named-doc      deployment-uploads     5.1s rounds=5 tools=read_document_pages,read_document_pages,search_documents,read_document_pages,read_document_pages  fact ok; documents ok
PASS  balance        next-work              1.9s rounds=1 tools=search_documents,search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.3s rounds=1 tools=search_documents  fact ok; documents ok
PASS  gap-general    rhel-packages          0.8s rounds=1 tools=offer_web_search  wrote no text; offered the search
PASS  live           ollama-release         0.7s rounds=1 tools=offer_web_search  offered the search; 0 web calls
PASS  web-read       web-read               26.1s rounds=1 tools=web_search,read_web_pages  offered the search; fact ok; near-miss avoided; 2 web calls
-- run 2
FAIL  overview       features               3.7s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown,…
PASS  procedure      deploy-and-test        5.1s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok
PASS  code           start-command          1.2s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  code           quickstart-command     2.1s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok
PASS  code           test-command           1.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
FAIL  exact-label    session-lifetimes      1.5s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
PASS  exact-label    oidc-issuer            1.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  named-doc      user-guide-pins        1.3s rounds=1 tools=search_documents  fact ok; documents ok
PASS  named-doc      deployment-uploads     6.0s rounds=1 tools=search_documents  fact ok; documents ok
PASS  balance        next-work              2.4s rounds=1 tools=search_documents,search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.4s rounds=1 tools=search_documents  fact ok; documents ok
PASS  gap-general    rhel-packages          1.5s rounds=2 tools=search_documents,offer_web_search  wrote no text; offered the search
PASS  live           ollama-release         0.8s rounds=1 tools=offer_web_search  offered the search; 0 web calls
PASS  web-read       web-read               25.6s rounds=1 tools=web_search,read_web_pages  offered the search; fact ok; near-miss avoided; 2 web calls
-- run 3
FAIL  overview       features               3.5s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown,…
PASS  procedure      deploy-and-test        5.2s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok
PASS  code           start-command          1.3s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  code           quickstart-command     2.5s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok
PASS  code           test-command           1.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
FAIL  exact-label    session-lifetimes      1.7s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3)
PASS  exact-label    oidc-issuer            1.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  named-doc      user-guide-pins        1.5s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  named-doc      deployment-uploads     3.9s rounds=4 tools=read_document_pages,read_document_pages,search_documents,read_document_pages  fact missing (client_max_body_size, 100m|100 mb|100mb); documents ok
PASS  balance        next-work              1.8s rounds=1 tools=search_documents,search_documents  fact ok; documents ok
PASS  balance        changelog-unreleased   1.3s rounds=1 tools=search_documents  fact ok; documents ok
PASS  gap-general    rhel-packages          1.6s rounds=2 tools=search_documents,offer_web_search  wrote no text; offered the search
PASS  live           ollama-release         0.7s rounds=1 tools=offer_web_search  offered the search; 0 web calls
PASS  web-read       web-read               50.1s rounds=2 tools=web_search,read_web_pages,read_web_pages  offered the search; fact ok; near-miss avoided; 3 web calls
tally: overview 0/3, procedure 3/3, code 9/9, exact-label 4/6, named-doc 5/6, balance 6/6, gap-general 3/3, live 3/3, web-read 3/3
right-document citations 28/33
web calls 7
36/42 passed
```

Headline: 36/42 passed over three runs (the previous layer passed 3/14 in one run), right-document citations 28/33. Almost
every failure is a citation form or a short answer, not a missing fact (see the table): `features` (overview: 3-4 of 6 terms with
4 needed, and 1-2 documents cited where 3 are needed), `session-lifetimes` (2 of 3 runs: `(ARCHITECTURE.md, page 3; USER_GUIDE.md,
page 1)`, two documents in one parenthesis, which the score does not read; the same joined form the baseline section noted), and
`deployment-uploads` in run 3 (the fact missing: "the specific configuration fix … is not explicitly listed in the retrieved snippets").

### Facts found per case (variant 9; the answer's fact, apart from citation form)

Previous layer = the `web` section's single run; this layer = the three runs of the second attempt above; rounds in brackets.

| case | previous layer (web) | **this layer (strategy)** |
|---|---|---|
| features | 4 of 6 terms [2] | **4, 3, 3 of 6 terms [2,2,2]** |
| deploy-and-test | 1/1 [2] | **3/3 [2,2,2]** |
| start-command | 1/1 [1] | **3/3 [1,1,1]** |
| quickstart-command | 1/1 [2] | **3/3 [2,2,2]** |
| test-command | 1/1 [1] | **3/3 [1,1,1]** |
| session-lifetimes | 1/1 [1] | **3/3 [1,1,1]** |
| oidc-issuer | 1/1 [1] | **3/3 [1,1,1]** |
| user-guide-pins | 1/1 [1] | **3/3 [1,1,1]** |
| deployment-uploads | 1/1 [1] | **2/3 [5,1,4]** |
| next-work | 1/1 [1] | **3/3 [1,1,1]** |
| changelog-unreleased | 1/1 [1] | **3/3 [1,1,1]** |
| rhel-packages | 0/1 [2] (empty answer, then the offer) | **no text to check [1,2,2]; offered 3/3** |
| ollama-release | no fact to check [0] (declined, no offer) | **no fact to check [1,1,1]; offered 3/3, 0 web calls** |
| web-read | 1/1 [1] (offered: no) | **3/3 [1,1,2]; offered 3/3** |

### Variant 50, `--repeat 1`

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 50)
FAIL  overview       features               4.7s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown,…
FAIL  procedure      deploy-and-test        8.1s rounds=2 tools=document_outline,document_outline,read_document_pages,read_document_pages  fact missing (./startup.sh start, pytest server/tests); documents ok
PASS  code           start-command          1.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  code           quickstart-command     2.5s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok
PASS  code           test-command           2.7s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  exact-label    session-lifetimes      1.8s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  exact-label    oidc-issuer            2.3s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
PASS  named-doc      user-guide-pins        2.3s rounds=1 tools=search_documents  fact ok; documents ok
PASS  named-doc      deployment-uploads     4.4s rounds=4 tools=read_document_pages,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok
FAIL  balance        next-work              3.2s rounds=2 tools=search_documents,read_document_pages  fact missing (HANDOVER); not cited (DOCS_INDEX.md)
PASS  balance        changelog-unreleased   2.2s rounds=1 tools=search_documents  fact ok; documents ok
FAIL  gap-general    rhel-packages          4.4s rounds=2 tools=search_documents,read_document_pages  fact ok; didn't say the documents don't cover it; no offer
PASS  live           ollama-release         1.7s rounds=1 tools=offer_web_search  offered the search; 0 web calls
FAIL  map-shortened  features-50            5.1s rounds=2 tools=search_documents,read_document_pages  3/6 terms (need 4): text-to-speech|read aloud|reads aloud|kokoro, chat, library, accounts|sign in|sign-in|login|log in|keycloak|oidc, pdf|markdown,…
PASS  map-shortened  start-command-50       2.0s rounds=1 tools=search_documents  fact ok; documents ok; pages ok
right-document citations 10/13
web calls 0
10/15 passed
```

10/15 passed, right-document citations 10/13 (the baseline: 1/15 passed, 1/13).

Facts found, against the map layer's variant 50 run (which found every fact but `next-work` and `features`): the same two
missing, plus **`deploy-and-test` newly missing** (below), and `features-50` 3 of 6 terms (it was 5 of 6). One run each cannot
separate that from noise, but the `deploy-and-test` miss has a cause that is the new tool:

- **`document_outline` was called on variant 50: yes, twice, in one turn** (`deploy-and-test`: `document_outline, document_outline,
  read_document_pages, read_document_pages`). The map layer's note was "never called, in 93 turns". It was the only turn of 15
  that called it. It did not call `search_documents` there, read DEPLOYMENT.md and TESTING.md by outline, and ended with
  "consult TESTING.md for the specific execution commands": the `pytest server/tests` command was on a page it didn't read. So
  the outline led to a shorter trail, and a worse answer, in the one turn where it was used. (It is offered in every turn of this variant: the 50-document map is shortened, as the map layer measured; not re-checked here.)
- `next-work` is unchanged: the model read CHAT_WITH_PDF p8 and LIBRARY p6 ("What's next") and did not reach DOCS_INDEX.

### The question the documents don't answer (`rhel-packages`)

- **Variant 9: now offers, 3/3 in each attempt (6/6).** Every time the model called `offer_web_search` (in 3 of the 6 turns
  at once, in the other 3 after one `search_documents`) and wrote **no text at all** before it, so the answer field is empty; the case passes under the new scoring
  (an offer with nothing invented). The tool's own description asks the model to "write first what you can say without the
  web": gemma4:31b-cloud does not. So the user sees the offer card with its reason and no sentence saying the documents don't cover
  RHEL. The rules' step 4 says to say so plainly, but a turn that calls the offer first never reaches step 4. That is a model habit
  the strategy text did not change; whether to ask for the sentence first is the next decision.
- **Variant 50: no offer, FAIL (1 run).** The model searched and read, and answered in prose: "The provided documents do not list
  specific RHEL 9 system packages required…", then described the container image and Podman/Docker from DEPLOYMENT.md with "(DEPLOYMENT.md,
  page 1)". It is a refusal in words, but the score's refusal pattern does not match "do not list", and it offered nothing, so it
  is scored as failing; the scorer was not widened.

### The web-read case: does it read the approved results?

**Yes, 6/6 across the two attempts.** In each, turn 1 (the question as typed) ended in an offer (`offered the search`), and turn 2
(the user's yes) ran the app's one search (the fake web was asked exactly one query each time), then the model called
`read_web_pages` once or twice, only on the 4.18 install guide (`https://docs.marrowdeck.example/4.18/install`, never the archived page listed
first), and answered `marrowdeck-srv` with the minimum version 4.18.3, not the near-miss 4.17.3. Every answer ends the web
fact with the site, as the new step 4 asks: "(docs.marrowdeck.example)". The model never called `web_search` itself in the approved
turn (it isn't offered); the `web_search` in the tool list is the app's. Time for turn 2 was 24-50 s (the reader, `llama3.2:3b` on this CPU).
Previous layer: turn 1 did not offer ("say you can't look it up here" was still the rule) and the facts were found anyway.

### What changed against the previous layer

- The offer works end to end as designed: `live` (3/3 in the single-document eval, 3/3 in each project attempt), `rhel-packages` on variant 9 (offers)
  and `web-read` (offer, yes, read) all behave. The text that says "ask first" is what moved them: with the old line the model
  declined live questions in one step, with no offer.
- The single-document eval did not drop anywhere (24/27 to 27/27; only `live` moved).
- Facts found in variant 9 are the same as before in every case but `deployment-uploads` (one miss in three runs, the fact stated as
  not in the snippets) and the `features` overview (3-4 of 6 terms, as before). The pass rate (36/42) is still decided by the citation
  form, not by the strategy: joined citations and an overview that cites 1-2 documents.
- Not measured here, and named: the labelled-general-knowledge half of the gap rule. `absent` (single document) refused plainly and
  added nothing; `rhel-packages` took the offer path. No run produced a "not from the documents" paragraph, so whether the model
  labels it correctly, and gives it no page, is untested by these runs.


## Layer: citations

- Date: 2026-10-19.
- Change (C2.1 Task 15, uncommitted at the time of the runs; HEAD `c66f648`): the server turns a project answer's citations into
  source chips by document, and the web pages a reader returned facts from into web source chips (`server/chat/sources.py`,
  `server/chat/orchestrator.py`). What the model reads did not change in this task (`prompt.py` is untouched), so every
  difference in the facts below is run-to-run noise, not this change. New in what a reply is saved with:
  - a project reply saves a `scope` note (`{ref, docId, name}` per document, the inert names the model was given) and a `sources`
    note by document (`{documents: [{docId, docName, pages, used}]}`), sent as `data-context` and `data-sources`;
  - any reply saves a `web_sources` note (`{pages: [{n, title, url}]}`, only `http(s)` links, only pages the reader got facts from),
    sent as `data-web-sources`;
  - `cited_pages` reads `(Name, page N)`, and now also joined citations (`(A.md, page 3; B.md, page 1)`), `page`/`pages`/`p.`/`pp.`,
    ranges up to ten pages and lists (`pages 4 and 6`).
- Scoring: **unchanged** (pass and fail as before). The report gains the citations columns, read back from what each reply was
  *saved* with (the `sources` and `web_sources` notes in `chat_messages.doc_context`, not the stream): `chip doc` (the note names the
  document the case expects), `chip page` (it names the expected page under that document), and for the web-read case `web chip`.
  `(searched only)` marks a document the note lists with `used: false`, i.e. one the model was given but whose pages the answer
  did not cite or share a run with.
- Model: `ollama:gemma4:31b-cloud`, thinking off, `DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_eval`.
  Reader: the default `llama3.2:3b`, fake web from `--web-fixture` (variant 9 only). One run each (`--repeat 1`), both finished with no
  provider error (exit status 1 = the model failed cases; 2 would be a harness failure).

### Variant 9, `--repeat 1 --web-fixture`

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 9)
FAIL  overview       features               6.1s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 1 documents cited (need 3)
PASS  procedure      deploy-and-test        6.7s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages  fact ok; documents ok; chip doc ok
PASS  code           start-command          1.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  code           quickstart-command     3.1s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  code           test-command           3.0s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
FAIL  exact-label    session-lifetimes      1.5s rounds=1 tools=search_documents  fact ok; not cited (ARCHITECTURE.md); page missing (want ARCHITECTURE.md p. 3); chip doc ok; chip page ok
PASS  exact-label    oidc-issuer            2.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  named-doc      user-guide-pins        1.4s rounds=1 tools=search_documents  fact ok; documents ok; chip doc ok
PASS  named-doc      deployment-uploads     98.2s rounds=5 tools=read_document_pages,read_document_pages,search_documents,read_document_pages,extract_from_documents  fact ok; documents ok; chip doc ok
FAIL  balance        next-work              2.2s rounds=1 tools=search_documents  fact missing (HANDOVER); documents ok; chip doc ok
PASS  balance        changelog-unreleased   1.8s rounds=1 tools=search_documents  fact ok; documents ok; chip doc ok
PASS  gap-general    rhel-packages          1.7s rounds=1 tools=offer_web_search  wrote no text; offered the search
PASS  live           ollama-release         1.7s rounds=1 tools=offer_web_search  offered the search; 0 web calls
PASS  web-read       web-read               35.7s rounds=1 tools=web_search,read_web_pages  offered the search; fact ok; near-miss avoided; 2 web calls; web chip: docs.marrowdeck.example
right-document citations 9/11
source chips: documents 10/10, pages 5/5
web source chips 1/1
web calls 2
11/14 passed
```

11/14 passed. The chip columns (new) say the saved sources note named the expected document in **10 of 10** cases that list one,
and the expected page in **5 of 5** cases that list one; none of the 15 entries was `searched only`. The web-read case produced
a web source chip: **yes**, one page, the 4.18 install guide, `https://docs.marrowdeck.example/4.18/install` (the archived page the
search lists first was never read, so it is not a source).

### Variant 50, `--repeat 1` (no web fixture)

```text
Project QA eval: ollama:gemma4:31b-cloud (variant 50)
FAIL  overview       features               4.9s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 2 documents cited (need 3)
PASS  procedure      deploy-and-test        5.4s rounds=2 tools=search_documents,search_documents,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok; chip doc ok
PASS  code           start-command          1.3s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  code           quickstart-command     2.3s rounds=2 tools=search_documents,read_document_pages  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  code           test-command           1.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  exact-label    session-lifetimes      1.6s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  exact-label    oidc-issuer            1.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
PASS  named-doc      user-guide-pins        1.4s rounds=1 tools=search_documents  fact ok; documents ok; chip doc ok
PASS  named-doc      deployment-uploads     8.9s rounds=4 tools=read_document_pages,read_document_pages,read_document_pages,read_document_pages  fact ok; documents ok; chip doc ok
FAIL  balance        next-work              3.0s rounds=2 tools=search_documents,read_document_pages  fact missing (HANDOVER); not cited (DOCS_INDEX.md); chip doc ok (searched only)
PASS  balance        changelog-unreleased   1.5s rounds=1 tools=search_documents  fact ok; documents ok; chip doc ok
PASS  gap-general    rhel-packages          13.7s rounds=2 tools=search_documents,read_document_pages  fact ok; said the documents don't cover it; no offer
PASS  live           ollama-release         0.8s rounds=1 tools=offer_web_search  offered the search; 0 web calls
FAIL  map-shortened  features-50            7.2s rounds=2 tools=search_documents,read_document_pages  4/6 terms (need 4); 1 documents cited (need 3)
PASS  map-shortened  start-command-50       1.4s rounds=1 tools=search_documents  fact ok; documents ok; pages ok; chip doc ok; chip page ok
right-document citations 10/13
source chips: documents 11/11, pages 6/6
web calls 0
12/15 passed
```

12/15 passed; chips named the expected document 11/11 and page 6/6, one document `searched only` (`next-work`: the model was
given DOCS_INDEX.md and did not use it, and its answer is also the one that misses HANDOVER).

### Facts found per case (the answer's fact, apart from citation form)

Previous layer = the strategy section's three variant-9 runs (rounds in brackets); this layer = one variant-9 run and one
variant-50 run. One run cannot be told from noise: the model-facing text is identical between the layers.

| case | previous layer (strategy, variant 9, 3 runs) | **this layer, variant 9** | **this layer, variant 50** |
|---|---|---|---|
| features | 4, 3, 3 of 6 terms [2,2,2] | **4 of 6 [2]**; 1 document cited | **4 of 6 [2]**; 2 documents cited |
| deploy-and-test | 3/3 [2,2,2] | **1/1 [2]** | **1/1 [2]** |
| start-command | 3/3 [1,1,1] | **1/1 [1]** | **1/1 [1]** |
| quickstart-command | 3/3 [2,2,2] | **1/1 [2]** | **1/1 [2]** |
| test-command | 3/3 [1,1,1] | **1/1 [1]** | **1/1 [1]** |
| session-lifetimes | 3/3 [1,1,1] | **1/1 [1]** (joined citation) | **1/1 [1]** |
| oidc-issuer | 3/3 [1,1,1] | **1/1 [1]** | **1/1 [1]** |
| user-guide-pins | 3/3 [1,1,1] | **1/1 [1]** | **1/1 [1]** |
| deployment-uploads | 2/3 [5,1,4] | **1/1 [5]** (98.2 s) | **1/1 [4]** |
| next-work | 3/3 [1,1,1] | **0/1 [1]** (HANDOVER missing) | **0/1 [2]** (HANDOVER missing) |
| changelog-unreleased | 3/3 [1,1,1] | **1/1 [1]** | **1/1 [1]** |
| rhel-packages | no text to check; offered 3/3 | **no text; offered** | **fact ok, said so in words; no offer tool call** |
| ollama-release | no fact to check; offered 3/3 | **offered, 0 web calls** | **offered, 0 web calls** |
| web-read | 3/3 [1,1,2]; offered 3/3 | **1/1 [1]; offered** | not asked |

### The citation columns, case by case (variant 9)

| case | expected | the eval's inline score (unchanged) | saved sources note names the document | names the page |
|---|---|---|---|---|
| deploy-and-test | DEPLOYMENT.md, TESTING.md | documents ok | ok | (no page listed) |
| start-command | README.md p. 3 | documents ok, pages ok | ok | ok |
| quickstart-command | README.md p. 3 | documents ok, pages ok | ok | ok |
| test-command | TESTING.md p. 1 | documents ok, pages ok | ok | ok |
| **session-lifetimes** | ARCHITECTURE.md p. 3 | **not cited, page missing** | **ok** | **ok** |
| oidc-issuer | DEPLOYMENT.md p. 2 | documents ok, pages ok | ok | ok |
| user-guide-pins | USER_GUIDE.md | documents ok | ok | (no page listed) |
| deployment-uploads | DEPLOYMENT.md | documents ok | ok | (no page listed) |
| next-work | DOCS_INDEX.md | documents ok | ok | (no page listed) |
| changelog-unreleased | DOCS_INDEX.md | documents ok | ok | (no page listed) |

### What this layer shows

- **Joined citations are now read where the chips are built, not where the eval scores.** In this run `session-lifetimes` answered
  `SESSION_IDLE_HOURS: 12 (USER_GUIDE.md, page 1; ARCHITECTURE.md, page 3)` and `SESSION_TTL_HOURS: 168 (ARCHITECTURE.md, page 3;
  USER_GUIDE.md, page 1)`. The eval's own inline pattern reads one citation per pair of parentheses, so that case is `FAIL` (`not
  cited (ARCHITECTURE.md)`), while the saved sources note names ARCHITECTURE.md page 3 under `used: true`. This is the case the strategy
  layer lost in two of three runs for the same reason. The eval's scoring was left alone on purpose (rulings): the pass rate
  still counts a joined citation as no citation, and the chip column shows what the reader of the saved chat would be offered.
- **The chip columns do not make a weak answer pass.** `features` cites one or two documents (it needs three; the markdown list
  ends with a single `(README.md, page 1)`), and `next-work` misses the fact HANDOVER; both fail as before. Whether the sources note of
  an overview names three documents is not measured: an overview lists no expected document, so it has no chip column (the number of
  documents in its note is in the saved reply, not in this report).
- **"Named" is not "used".** A document counts as named when the note lists it at all; `used` separates the pages the answer cited or
  shared a distinctive run with from the pages the model was only given. Every expected document was `used` in variant 9; the one
  exception in variant 50 is marked `(searched only)`. A refusal produces a note of everything searched, labelled so (the
  per-document form of the "Searched (not cited)" chip the single-document chat already has).
- **Web source chips.** The web-read case's answer ends `(docs.marrowdeck.example)` and the reply was saved with one web source,
  the page whose facts it used. A page the reader found nothing on, a page read in a call the turn's budget dropped, and a result
  whose URL is not `http(s)` (the `javascript:` and `data:` cases) are never listed; those are unit-tested
  (`test_chat_sources.py`, `test_chat_orchestrator.py`), not exercised by the fake web.
