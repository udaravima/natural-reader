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
