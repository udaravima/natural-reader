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

## Final (cloud)

The last cloud measurement of C2.1 project chat, against the spec's section 2 targets. The local `gemma4:e4b` run is a separate
step and is appended below this section when it has been made.

- Dates: the cloud runs 2026-10-19; the rebuild timing and the gates 2026-10-20.
- Code under test: `ec297f4` (`fix(chat): the approved search query is cleaned on the server too`). The tree has moved to docs-only
  commits since (`git diff ec297f4 HEAD` touches `.env.example`, `CHANGELOG.md`, `README.md`, `docs/` and the spec, no code).
- Model: `ollama:gemma4:31b-cloud`, thinking off; chat web mode `ask` (the eval's default for every case). Scratch database
  `natural_reader_eval`. Reader: the default `llama3.2:3b`. Fake web only in variant 9 (`--web-fixture`).
- Commands, as briefed (`--show-answers` added so the answers could be quoted; it changes only what is printed):

  ```sh
  DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_eval \
    .venv/bin/python scripts/eval_project_qa.py --model ollama:gemma4:31b-cloud --variant 9  --repeat 3 --web-fixture
  DATABASE_URL=... same, --variant 50 --repeat 3
  ```

  Both ran through a thin wrapper kept outside the repository (it calls the script's own `main()` and logs each chat turn: its
  question, whether it carried `context.web`, the tool names it called). It does not change what the eval does; it is how the
  unapproved web calls below were counted exactly.
- **Runs, all reported (nothing dropped):**

  | run | result | note |
  |---|---|---|
  | variant 9, `--repeat 3 --web-fixture` | 37/42 passed, exit 1 | one attempt, no provider error |
  | variant 50, `--repeat 3`, **attempt 1** | 29/45 passed, exit 2 | one turn ended `provider_unavailable` (`features-50`, run 3, 18.4 s into the turn); a provider failure says nothing about the rules, so the variant was run once more |
  | variant 50, `--repeat 3`, **attempt 2** | 32/45 passed, exit 1 | no error; both attempts are shown below, none is preferred |

- Which results survived the loss of the scratch Postgres (a container restart wiped its tmpfs on 2026-10-20): all of them. The
  numbers above are read from the saved stdout, stderr and per-turn logs of these runs, not from the database. Nothing in
  `natural_reader_eval` was needed afterwards; the next eval run indexes the fixture again first. What the restart did cost:
  a first, half-finished rebuild-timing database (`natural_reader_rebuild`) was lost with it, so that measurement was made again
  from the start (twice, below).

### The spec section 2 targets

| target (spec section 2, "our call", cloud model) | measured | met |
|---|---|---|
| at least 90% right-document citations on cases with a document answer | **variant 9: 29/33 = 87.9%.** Variant 50: attempt 1 28/38 = 73.7%, attempt 2 28/39 = 71.8%. (The eval's own line, "right-document citations": the answer cites every document the case names, in the form `(Name, page N)`, and none it forbids.) Read from the sources notes the replies were saved with (the chips): the expected document was named in 30/30 (variant 9), 32/33 and 33/33 (variant 50), and the expected page in 15/15, 18/18 and 18/18. | **no** |
| the exact command on the code case | `code` cases (start, quickstart, test): variant 9 9/9; variant 50 attempt 1 9/9, **attempt 2 8/9**. The one miss: asked for the Linux quickstart command, the answer gave `./startup.sh start` (README.md, page 3) instead of `./startup.sh quickstart`. The variant-50 `start-command-50` also passes 3/3 and 3/3. | **no, 26 of 27 turns** (every run of variant 9, and attempt 1, were exact) |
| 0 unapproved web calls | **0** `web_search` / `read_web_pages` calls in the 132 turns that carried no `context.web` (42 + 45 + 45); the 3 approved turns made 7 calls (below). | **yes** |
| 0 invented pages on gap cases | the `gap-general` case (`rhel-packages`): variant 9 0 of 3 flagged, but in all three the model called `offer_web_search` and wrote no text, so there was nothing to cite a page in. Variant 50: **attempt 1 flagged 2 of 3** (`cited pages [1] for the part they don't cover`), attempt 2 0 of 3. | **no** (2 of 9 gap turns flagged; one attempt clean) |

Two things about the first row. The 87.9% is the eval's *inline* citation rule, which counts a *joined* citation
`(ARCHITECTURE.md, page 3; USER_GUIDE.md, page 1)` as no citation (the Task 15 ruling left that unchanged on purpose) and requires an
overview answer to cite at least three distinct documents. Of the four variant-9 turns that miss, three are the overview
(`features`, 1-2 documents cited each time) and one is a joined citation whose document and page the saved chips do name. If a joined
citation counted, variant 9 would be 30/33 = 90.9% (variant 50: 31/38 and 30/39), which is my arithmetic, not a score the eval
gives. The overview misses would remain. Second, the chip figures leave out the cases that list no expected document (the
overview), so they cannot show the overview shortfall.

### How the unapproved web calls were counted

A web request leaves the server only through `web_search` or `read_web_pages` (`offer_web_search` is the offer; it sends nothing).
"Unapproved" is one of those two tools running in a turn that did not carry the user's yes (`context.web` / `web_query`), in
`ask` mode. The eval's own `web calls N` line is not that number: it counts the *scored* turn, and for the `web-read` case the scored
turn is the approved one. So the count is from the wrapper's per-turn log, which includes the `web-read` case's first (offer) turn:

| run | turns | turns without `context.web` | web calls in them | turns with `context.web` | web calls in them |
|---|---|---|---|---|---|
| variant 9 | 45 | 42 | **0** | 3 | 7 (per turn: the app's own approved `web_search`, then the model's `read_web_pages`; one turn read twice) |
| variant 50, attempt 1 | 45 | 45 | **0** | 0 | 0 |
| variant 50, attempt 2 | 45 | 45 | **0** | 0 | 0 |

A second, independent check in variant 9: the fake web recorded exactly one search query per run (three queries, the approved
query each time) and four page fetches (all of the one 4.18 install page), all inside the approved turns. The eval's `web calls`
lines read 7 for variant 9 (the approved calls) and 0 for both variant-50 attempts. `offer_web_search` was called in 9 of the 42
variant-9 turns (the `rhel-packages`, `ollama-release` and `web-read` questions, three runs each) and in 3 of 45 variant-50 turns
per attempt (`ollama-release`).

### Variant 9, `--repeat 3 --web-fixture` (37/42 passed; right-document citations 29/33)

Per-kind tally: overview 0/3, procedure 3/3, code 9/9, exact-label 5/6, named-doc 5/6, balance 6/6, gap-general 3/3, live 3/3,
web-read 3/3. Source chips: documents 30/30 (0 searched only), pages 15/15. Web source chips 3/3 (the 4.18 install page every
time). `fact found` is the answer's fact apart from the citation form; `right document` and `page` are the inline score (a case
that lists no page shows `-`); `chip doc` / `chip page` are what the saved sources note names.

| case | kind | passed | fact found | right document (inline) | page (inline) | chip doc | chip page | rounds per run |
|---|---|---|---|---|---|---|---|---|
| features | overview | 0/3 | 2/3 | 0/3 | - | - | - | 2 / 2 / 2 |
| deploy-and-test | procedure | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 2 / 2 / 2 |
| start-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| quickstart-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 2 / 2 / 2 |
| test-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| session-lifetimes | exact-label | 2/3 | 3/3 | 2/3 | 2/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| oidc-issuer | exact-label | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| user-guide-pins | named-doc | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| deployment-uploads | named-doc | 2/3 | 2/3 | 3/3 | - | 3/3 | - | 1 / 4 / 4 |
| next-work | balance | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| changelog-unreleased | balance | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| rhel-packages | gap-general | 3/3 | - | - | - | - | - | 1 / 2 / 1 |
| ollama-release | live | 3/3 | - | - | - | - | - | 1 / 1 / 1 |
| web-read | web-read | 3/3 | 3/3 | - | - | - | - | 1 / 2 / 1 |

### Variant 50, `--repeat 3` (no web fixture), both attempts

Attempt 1: 29/45 passed (one turn `provider_unavailable`, so exit 2); attempt 2: 32/45 passed. Per-kind tally, attempt 1: overview
0/3, procedure 1/3, code 9/9, exact-label 3/6, named-doc 6/6, balance 3/6, gap-general 1/3, live 3/3, map-shortened 3/6 (one of
the three errored); attempt 2: overview 0/3, procedure 2/3, code 8/9, exact-label 4/6, named-doc 6/6, balance 3/6, gap-general 3/3,
live 3/3, map-shortened 3/6. Right-document citations 28/38 and 28/39. Source chips: documents 32/33 and 33/33 (the second
with one `searched only`), pages 18/18 and 18/18. Web calls 0 in both. "a1 / a2" = attempt 1 / attempt 2.

| case | kind | passed (a1 / a2) | fact found (a1 / a2) | right document, inline (a1 / a2) | page, inline (a1 / a2) | chip doc (a1 / a2) | chip page (a1 / a2) |
|---|---|---|---|---|---|---|---|
| features | overview | 0/3 / 0/3 | 1/3 / 3/3 | 0/3 / 0/3 | - / - | - / - | - / - |
| deploy-and-test | procedure | 1/3 / 2/3 | 2/3 / 3/3 | 2/3 / 2/3 | - / - | 2/3 / 3/3 | - / - |
| start-command | code | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 |
| quickstart-command | code | 3/3 / 2/3 | 3/3 / 2/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 |
| test-command | code | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 |
| session-lifetimes | exact-label | 0/3 / 1/3 | 3/3 / 3/3 | 0/3 / 1/3 | 0/3 / 1/3 | 3/3 / 3/3 | 3/3 / 3/3 |
| oidc-issuer | exact-label | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 |
| user-guide-pins | named-doc | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | - / - | 3/3 / 3/3 | - / - |
| deployment-uploads | named-doc | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | - / - | 3/3 / 3/3 | - / - |
| next-work | balance | 0/3 / 0/3 | 0/3 / 0/3 | 2/3 / 1/3 | - / - | 3/3 / 3/3 | - / - |
| changelog-unreleased | balance | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | - / - | 3/3 / 3/3 | - / - |
| rhel-packages | gap-general | 1/3 / 3/3 | 3/3 / 3/3 | - / - | - / - | - / - | - / - |
| ollama-release | live | 3/3 / 3/3 | - / - | - / - | - / - | - / - | - / - |
| features-50 | map-shortened | 0/3 (1 error) / 0/3 | 2/2 / 2/3 | 0/2 / 0/3 | - / - | - / - | - / - |
| start-command-50 | map-shortened | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 / 3/3 |

### Every miss, case by case

The inline score decides pass and fail. "Excerpt" is the start of the model's answer (or the part that shows the miss).
a1 / a2 = variant-50 attempt 1 / 2; v9 = variant 9.

**`features`** (overview; needs 4 of 6 terms and at least 3 distinct documents cited). Failed in **all 9 turns** (v9 0/3, a1 0/3, a2
0/3) and `features-50` in all 5 of 5 that finished (a1 0/2 and one error, a2 0/3). In every finished turn the model searched, read pages, and wrote a list under the same four headings (Document
Viewing; Text-to-Speech; Local AI Chat; Additional Tools, with a Multi-User heading in 3 of the 14 that finished). Its
citations are README.md plus, in 9 of the 14, one or two USER_GUIDE.md lines; LIBRARY.md, IDENTITY_AND_ROLES.md, DEPLOYMENT.md
and the rest are never cited in a features answer. Terms and documents
cited by turn:

| turn | terms of 6 | documents cited | what the answer cites |
|---|---|---|---|
| v9 run 1 | 3 (missing: library, accounts, admin/project) | 1 | README.md only |
| v9 run 2 | 4 | 2 | README.md, USER_GUIDE.md (CHAT_WITH_PDF.md sits in a joined citation, not read) |
| v9 run 3 | 4 | 2 | README.md, USER_GUIDE.md |
| a1 run 1 / run 3 | 3 / 3 | 1 / 1 | README.md only |
| a1 run 2 | 4 | 2 | README.md, USER_GUIDE.md |
| a2 runs 1, 2, 3 | 4, 4, 4 | 2, 2, 2 | README.md, USER_GUIDE.md |
| `features-50` a1 run 1 / run 2 | 4 / 4 | 2 / 1 | README.md (+ USER_GUIDE.md in run 1) |
| `features-50` a1 run 3 | - | - | **ERROR** `provider_unavailable`, not a model miss |
| `features-50` a2 run 1 / 2 / 3 | 4 / 4 / 3 | 2 / 2 / 1 | README.md (+ USER_GUIDE.md in runs 1 and 2) |

Excerpt (v9 run 1): "Neural Reader is a modern document reader with the following key features: ### Document Viewing ... (README.md,
page 1) ... ### Text-to-Speech (TTS) ... ### Local AI Chat (Ollama) ... ### Additional Tools (v1.8.0+) ..." The answer
cites `(README.md, page 1)` 15 times and nothing else.

**`session-lifetimes`** (exact-label, expects ARCHITECTURE.md p. 3). 6 failing turns (v9 run 1; a1 runs 1-3; a2 runs 1-2). All six
have the fact right (168 and 12) and all six cite in the **joined** form the inline rule does not read, so the verdict is `not
cited (ARCHITECTURE.md); page missing`; the saved chips name ARCHITECTURE.md and page 3 in all six. Excerpt (the same shape every time):
"SESSION_TTL_HOURS: 168 (which equals 7 days) (ARCHITECTURE.md, page 3; USER_GUIDE.md, page 1) / SESSION_IDLE_HOURS: 12
(ARCHITECTURE.md, page 3; USER_GUIDE.md, page 1)". A form miss, not a content miss.

**`next-work`** (balance, fact `HANDOVER`). v9 3/3 pass; **variant 50 fails 6 of 6** (a1 and a2, fact missing every time). The padding
documents make the "What's next" sections win: a1 run 1 "there are a few places where future or next steps are mentioned: CHAT_WITH_PDF.md
... page 8 'What's next' ... LIBRARY.md ... page 6 ... DOCS_INDEX.md: Mentions that post-v1.9.0 work (multi-user auth and the inference
gateway) is covered by the documentation set ..." In a1 run 2 and a2 runs 1 and 2 it also names DOCS_INDEX.md in bold with "(page 1)",
not in the `(Name, page N)` form, so `not cited (DOCS_INDEX.md)` as well. The pointer to `../HANDOVER.md` that DOCS_INDEX.md carries
is never relayed. In v9 the same question was answered with it (3/3).

**`deploy-and-test`** (procedure; fact `./startup.sh start` and `pytest server/tests`; cites DEPLOYMENT.md and TESTING.md; v9 passes
3/3). Variant 50 fails 3 of 6, three different ways. a1 run 1: fact ok, but `not cited (DEPLOYMENT.md, TESTING.md)`: a long walk-through
(production deployment, then `podman-compose up -d postgres` and `.venv/bin/python -m pytest server/tests/ -q`) with a single
citation at its very end, in the joined form: "(DEPLOYMENT.md, page 5; TESTING.md, page 1)". a1 run 2: **fact missing**: it has
`./startup.sh start` (DEPLOYMENT.md, page 5) but, for the tests, only "To run the tests, refer to the 'How to run' section in
`TESTING.md` (page 1)" and no command; it cites TESTING.md six times while its saved sources note lists no TESTING.md page at all
(`chip doc missing (TESTING.md)`), so those citations were not backed by a page the turn had retrieved (inferred from the note;
the per-turn log keeps tool names, not their arguments). a2 run 3: fact ok, `not cited (TESTING.md)`: it gives
`.venv/bin/pytest server/tests` and `npm run test:run` and cites them to README.md, page 10; TESTING.md is `searched only`.

**`quickstart-command`** (code; a2 run 3). Fact missing. The whole answer: "Based on the Quickstart (Linux) section in the
documentation, the command used to start the whole stack in live mode is: `./startup.sh start` (README.md, page 3)". Right document and
page, wrong command: the neighbouring `start` instead of `./startup.sh quickstart`.

**`deployment-uploads`** (named-doc; v9 run 3). Fact missing (`100m`). The answer gives the cause and says the fix "is not explicitly
detailed in the provided passages": "larger PDF uploads fail because nginx has a default `client_max_body_size` of 1 MB (DEPLOYMENT.md,
page 4) ... 413 Request Entity Too Large ... While the provided text identifies the cause, the specific fix (which would typically
involve increasing the `client_max_body_size` ...) is not explicitly detailed". It had read 3 passages in 4 rounds. The same question
passed in 5 of 6 other turns.

**`rhel-packages`** (gap-general; the invented-pages check). v9: 3/3 pass, but all three are `offer_web_search` with no text.
Variant 50 a1: **run 2 fail** - "cited pages [1] for the part they don't cover": the answer says the documents list no RHEL 9
packages, then a bullet "Containerized Deployment: ... pinned inside the image to prevent host OS upgrades (like those in RHEL 9)
from affecting the application (DEPLOYMENT.md, page 1)". **Run 3 fail** - "didn't say the documents don't cover it" (its first
sentence is "The provided documents do not list specific RHEL 9 system packages that you need to install", which the eval's refusal
wording check did not accept) and the same shape: "... a host OS upgrade (like moving to RHEL 9) will not affect the application's
interpreter (DEPLOYMENT.md, page 1)". My reading: the page backs the document's own statement about the container image; the
words "RHEL 9" are the model's gloss inside a cited sentence, which is the blur the rule exists to stop, so I count it as a miss
rather than excuse it. a2: 3/3 pass.

**`features-50` run 3 of attempt 1** is the one `ERROR` (provider), listed above; no answer to judge.

### Not a target row, but spec section 2 asks for it: the shape of the RHEL answer

Section 2 describes the answer as: says the documents don't cover RHEL; gives the documents' own requirements (cited); adds general
knowledge introduced as not from the documents (no page); ends with an offer to search the web for a short query. Measured, what the
user gets:

| | says the documents don't cover it | documents' own requirements, cited | general knowledge, labelled | offer to search the web |
|---|---|---|---|---|
| variant 9 (3 runs) | no text written | no | no | **yes**, the consent offer (`offer_web_search`) 3/3 |
| variant 50 attempt 1 (3 runs) | 3/3 in words (one phrased so the eval's check missed it) | 3/3 (container image, podman/docker, nginx; DEPLOYMENT.md page 1) | **none**; no package names beyond what the documents say | **none**, 0/3 |
| variant 50 attempt 2 (3 runs) | 3/3 | 3/3 | none | **in words in 2 of 3** ("would you like me to search the web for ...?"); run 1 none. The consent offer tool was called in 0/3 |

So the full shape in section 2 is not produced by either variant. In variant 9 the offer comes with no explanation; in variant 50 there
is an explanation and, mostly, no offer. A sentence-form offer is also not the consent card, so a "yes" typed under it would be an
ordinary turn that carries no `context.web`, and no search would run in `ask` mode. Neither is counted in the targets table, which
follows the "our call" sentence of section 2 only.

### What this layer shows

- Of the four section 2 targets, one is met (no unapproved web call, 0 of 132 turns). The citation target is missed on the
  eval's own rule in both variants (87.9% and about 72%), the code case has one wrong command in 27 turns, and the gap case had two
  flagged turns in one attempt.
- The citation shortfall has three causes that are not the same thing: the overview answer drawn from README.md alone (0 of 14
  finished turns across the two variants), the joined citation form (6 turns, content right and chips right), and variant 50's
  `next-work` / `deploy-and-test` where the document that matters is not reached or not cited. The chips are right far more often
  (30/30, 32/33, 33/33) than the inline text is, and they exclude the overview, so they flatter the overview-shaped weakness.
- Variant 9 against the strategy layer's three variant-9 runs (36/42, right-document 28/33): 37/42 and 29/33. The model-facing
  text was not changed between them in a way this table can separate from run-to-run noise.
- Variant 50 is clearly weaker than variant 9 on `next-work` (0/6 against 3/3) and `deploy-and-test` (3/6 against 3/3), where 41
  padding documents compete in the ranking. The two variant-50 attempts also differ by 3 passes on the same code (29 with an error
  against 32), so single digits of difference between layers are inside the noise.
- The user's three §1 questions, as the cloud model answers them today: the deploy-and-test walk-through is right in variant 9 and
  uneven in variant 50; the code questions are right (26/27); "what are the features" is answered from README.md plus, at most, a line or two of USER_GUIDE.md, in every turn;
  the RHEL question gets an offer without an explanation, or an explanation without the offer (above).

## Final (cloud, after Task 22)

The measurement after Task 22 closed the gaps that "Final (cloud)" found. The server and eval items: (a) an offer with no text gets a tools-off answer
step, (b) no offers in words, (b2) documents first, (c) a whole-project sentence (measured and **removed**, below), (d) the eval counts citations with the
product's parser, (g) web search settings warn and default, (h) the reader keeps partial answers and the tool line counts only pages with facts, (o) a
missing document in a project means the open one.

- Date: 2026-10-20. Code under test: the Task 22a working tree on `bf88b94`, uncommitted when measured (Task 22b's client files change nothing here).
- Model `ollama:gemma4:31b-cloud`, thinking off, chat web mode `ask`. Scratch database `natural_reader_eval`. **Reader: the default `llama3.2:3b`, as in
  "Final (cloud)"**, so the two are comparable (the brief's commands name no other; a cloud reader would change every `extract_from_documents` and `read_web_pages` result
  and could not be compared). Fake web only in variant 9 (`--web-fixture`). `--show-answers` was added so the answers could be read; the same kind of thin wrapper kept outside the
  repository as for "Final (cloud)" logged every chat turn (question, whether it carried `context.web`, tool names, steps, answer).
- **Runs, all reported, none dropped.** No provider error in any of them.

  | run | tree | result |
  |---|---|---|
  | variant 9, `--repeat 3 --web-fixture` | with (c) | 33/42 passed |
  | variant 50, `--repeat 3` | with (c) | 35/45 passed |
  | variant 9, `--repeat 3 --web-fixture` | **shipped (c removed)** | 36/42 passed |
  | variant 50, `--repeat 3` | **shipped (c removed)** | 35/45 passed |
  | diagnostic: `web-read` only, 6 runs each, the reader brief of (h) and the brief before it | shipped | 5/6 and 5/6 |
  | diagnostic: `rhel-packages` only, 6 runs, steps recorded | shipped | text and card in 5/6 |

- **A measurement change comes with this section.** Task 22 (d) makes the eval count citations with the product's own grammar (`server.chat.sources.cited_pages`), so a joined citation
  `(A.md, page 3; B.md, page 1)` names both documents, as the server counts it and the client links it. "Final (cloud)" was scored with the narrower pattern. Its answers were kept
  (`--show-answers`), so those three runs are **re-scored here with the new parser** from the saved output: variant 9 37/42 becomes 38/42 and right-document citations 29/33 becomes 30/33;
  variant 50 attempt 1 29/45 becomes 33/45 (28/38 becomes 32/38) and attempt 2 32/45 becomes 35/45 (28/39 becomes 31/39). The cases that change are `session-lifetimes` (6 turns, the joined citation),
  `deploy-and-test` (one turn, attempt 1) and `features` (one turn, attempt 2: three documents cited, one in joined form). The re-scoring takes the saved answer, the tool names and the offer flag
  from the saved report; it does not re-derive the source chips. The gap case's invented-page check keeps its own narrower pattern (the brief says not to change it), so a page cited in a joined citation
  inside a sentence about the gap would not be flagged.

### The spec section 2 targets

Shipped tree (no (c)). "Before" is "Final (cloud)", as published and, where the number moves, re-scored with (d).

| target | before | after | met |
|---|---|---|---|
| at least 90% right-document citations on cases with a document answer | variant 9: 29/33 = 87.9% (re-scored 30/33 = 90.9%); variant 50: 28/38 and 28/39 (re-scored 32/38 = 84.2% and 31/39 = 79.5%) | **variant 9: 30/33 = 90.9%; variant 50: 31/39 = 79.5%** (the tree with (c): 30/33 and 33/39 = 84.6%) | variant 9 yes, **variant 50 no**. The variant 9 gain is the measurement change, not the model |
| the exact command on the code case | 26 of 27 turns (variant 9 9/9; variant 50 9/9 and 8/9) | **variant 9 9/9; variant 50 9/9** (and `start-command-50` 3/3) | yes |
| 0 unapproved web calls | 0 of 132 turns | **0 of 87 turns** (42 + 45; the 3 approved turns made 7 calls). The runs with (c) and the diagnostics: 0 as well | yes |
| 0 invented pages on gap cases | 2 of 9 gap turns flagged (variant 50 attempt 1: 2 of 3; variant 9's three wrote no text, so nothing could be flagged) | **1 of 6 flagged** (variant 50, run 2: "(DEPLOYMENT.md, page 1)" inside a sentence about RHEL 9). Variant 9's three now wrote text, and none was flagged | **no** (one turn; the same blur as before) |

The user's three questions as the cloud model answers them now: the deploy-and-test walk-through is right in both variants (3/3 and 3/3); the code questions are right; "What are the features?"
is still wrong (below); the RHEL question gets its answer shape in variant 9 and not in variant 50 (below).

### Variant 9, `--repeat 3 --web-fixture` (36/42 passed; right-document citations 30/33), shipped tree

Per-kind tally: overview 0/3, procedure 3/3, code 9/9, exact-label 6/6, named-doc 5/6, balance 6/6, gap-general 2/3, live 3/3, web-read 2/3. Source chips: documents 30/30, pages 15/15.
Web source chips 3/3. Columns as in "Final (cloud)".

| case | kind | passed | fact found | right document (inline) | page (inline) | chip doc | chip page | rounds per run |
|---|---|---|---|---|---|---|---|---|
| features | overview | 0/3 | 2/3 | 0/3 | - | - | - | 2 / 2 / 2 |
| deploy-and-test | procedure | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 2 / 2 / 2 |
| start-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| quickstart-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 2 / 2 / 2 |
| test-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| session-lifetimes | exact-label | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| oidc-issuer | exact-label | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| user-guide-pins | named-doc | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| deployment-uploads | named-doc | 2/3 | 2/3 | 3/3 | - | 3/3 | - | 1 / 1 / 4 |
| next-work | balance | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| changelog-unreleased | balance | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| rhel-packages | gap-general | 2/3 | 3/3 | - | - | - | - | 2 / 2 / 2 |
| ollama-release | live | 3/3 | - | - | - | - | - | 1 / 1 / 1 |
| web-read | web-read | 2/3 | 2/3 | - | - | - | - | 1 / 1 / 2 |

### Variant 50, `--repeat 3` (no web fixture), shipped tree (35/45 passed; right-document citations 31/39)

Per-kind tally: overview 1/3, procedure 3/3, code 9/9, exact-label 6/6, named-doc 6/6, balance 3/6, gap-general 1/3, live 3/3, map-shortened 3/6. Source chips: documents 33/33, pages 18/18. Web calls 0.

| case | kind | passed | fact found | right document (inline) | page (inline) | chip doc | chip page | rounds per run |
|---|---|---|---|---|---|---|---|---|
| features | overview | 1/3 | 3/3 | 1/3 | - | - | - | 2 / 2 / 2 |
| deploy-and-test | procedure | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 2 / 2 / 2 |
| start-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| quickstart-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 2 / 2 / 2 |
| test-command | code | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| session-lifetimes | exact-label | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| oidc-issuer | exact-label | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |
| user-guide-pins | named-doc | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| deployment-uploads | named-doc | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 4 / 1 |
| next-work | balance | 0/3 | 0/3 | 0/3 | - | 3/3 | - | 2 / 2 / 2 |
| changelog-unreleased | balance | 3/3 | 3/3 | 3/3 | - | 3/3 | - | 1 / 1 / 1 |
| rhel-packages | gap-general | 1/3 | 3/3 | - | - | - | - | 2 / 2 / 2 |
| ollama-release | live | 3/3 | - | - | - | - | - | 1 / 1 / 1 |
| features-50 | map-shortened | 0/3 | 1/3 | 0/3 | - | - | - | 2 / 2 / 2 |
| start-command-50 | map-shortened | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1 / 1 / 1 |

### Before and after, case by case

Cases passed of 3. "As published" and "re-scored" are "Final (cloud)" (variant 50: attempt 1 / attempt 2); the third column is the tree with (c); the last is the shipped tree.

**Variant 9, cases passed of 3**

| case | Final (cloud), as published | Final (cloud), re-scored with (d) | after Task 22, with (c) | after Task 22, shipped (no (c)) |
|---|---|---|---|---|
| features | 0/3 | 0/3 | 0/3 | 0/3 |
| deploy-and-test | 3/3 | 3/3 | 0/3 | 3/3 |
| start-command | 3/3 | 3/3 | 3/3 | 3/3 |
| quickstart-command | 3/3 | 3/3 | 3/3 | 3/3 |
| test-command | 3/3 | 3/3 | 3/3 | 3/3 |
| session-lifetimes | 2/3 | 3/3 | 3/3 | 3/3 |
| oidc-issuer | 3/3 | 3/3 | 3/3 | 3/3 |
| user-guide-pins | 3/3 | 3/3 | 3/3 | 3/3 |
| deployment-uploads | 2/3 | 2/3 | 3/3 | 2/3 |
| next-work | 3/3 | 3/3 | 3/3 | 3/3 |
| changelog-unreleased | 3/3 | 3/3 | 3/3 | 3/3 |
| rhel-packages | 3/3 | 3/3 | 0/3 | 2/3 |
| ollama-release | 3/3 | 3/3 | 3/3 | 3/3 |
| web-read | 3/3 | 3/3 | 3/3 | 2/3 |

**Variant 50, cases passed of 3 (Final (cloud) columns: attempt 1 / attempt 2)**

| case | Final (cloud), as published | Final (cloud), re-scored with (d) | after Task 22, with (c) | after Task 22, shipped (no (c)) |
|---|---|---|---|---|
| features | 0/3 / 0/3 | 0/3 / 1/3 | 1/3 | 1/3 |
| deploy-and-test | 1/3 / 2/3 | 2/3 / 2/3 | 1/3 | 3/3 |
| start-command | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| quickstart-command | 3/3 / 2/3 | 3/3 / 2/3 | 3/3 | 3/3 |
| test-command | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| session-lifetimes | 0/3 / 1/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| oidc-issuer | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| user-guide-pins | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| deployment-uploads | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| next-work | 0/3 / 0/3 | 0/3 / 0/3 | 1/3 | 0/3 |
| changelog-unreleased | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| rhel-packages | 1/3 / 3/3 | 1/3 / 3/3 | 2/3 | 1/3 |
| ollama-release | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |
| features-50 | 0/3 / 0/3 | 0/3 / 0/3 | 0/3 | 0/3 |
| start-command-50 | 3/3 / 3/3 | 3/3 / 3/3 | 3/3 | 3/3 |

Some of the differences between the first and the last column are changes in what is measured, not in what the model does: `session-lifetimes`, one `features` turn and one `deploy-and-test` turn are (d); the
`rhel-packages` figures move with the eval's refusal wording (below). The rest are inside run-to-run noise, which is at least two passes on the same code (the two "Final (cloud)" variant 50 attempts differed by 3).

### What each item moved

- **(a) an offer with no text writes its answer.** The RHEL question in variant 9 went from the card alone (3 of 3, no text) to **text and card**, 3 of 3, and 5 of 6 in the diagnostic. In those five the
  turn had three steps (`search_documents`, `offer_web_search`, then the answer step) and ended `data-web-offer`, `data-sources`, `finish`: the extra step ran with the real model. The sixth answered from five
  document tool calls and did not offer. The live question `ollama-release` (the documents have nothing on it) used to be the card alone; it is now a one-sentence text and the card, 6 of 6 (in variant 50: two
  steps each), with 0 web calls.
- **(b) no offers in words.** Variant 50: 0 of 6 RHEL turns offered in words (before: 2 of 3 in attempt 2). They offered no card either (below), so the consent dead end is gone and the offer is still missing.
- **(b2) documents first, in ask mode.** Every RHEL turn that offered searched the documents first: 11 of 11 in variant 9 and the diagnostic (3 + 3 + 5; the first three on the tree with (c)), against the
  offer as the only call in 3 of 3 before. The documents list the stack on README page 3, so the answers now cite it (every text answer, 12 of 12, cites a document's own requirement).
- **(c) whole-project questions: removed.** See the decision below.
- **(d) citations.** In the re-scored table above: `session-lifetimes` 2/3 to 3/3 in variant 9 and 0/3 and 1/3 to 3/3 in variant 50, one `deploy-and-test` turn, one `features` turn.
- **(h) the reader keeps partial answers.** Unit tested: a scripted reader returns one fact for one of two parts and it is kept; the tool line counts pages with facts and lists the others as "Nothing found". In the
  eval the only reader case is `web-read` (one need with two parts, the package and the minimum version), and it did not move: 3/3 with (c), 2/3 shipped, and 5/6 against 5/6 in the diagnostic with the brief of (h)
  and the brief before it. The miss each time is the same shape: the answer gave the archived guide's 4.17.3 and not 4.18.3. **Watch item:** web source chips appeared in 4 of 6 `web-read` turns with the new brief
  and 6 of 6 with the old one; over every new-brief `web-read` turn this task (both variant 9 runs and the diagnostic) it is 10 of 12, against 9 of 9 for the old brief ("Final (cloud)" and the diagnostic). In the two chipless
  turns the answer was still right. Too few turns to call it, and I did not look at why (the reader here is the local `llama3.2:3b`, which can time out or return no verifiable quote).
- **(o) a missing document means the open one.** Not exercised: the eval opens no document (`doc_id` is `None`), so no document is ever open. Unit tested for all three tools.
- **(g) web search settings.** Not exercised by the eval. Unit tested, including that the module imports with a typo in a setting.

### Item (c), the decision

(c) was appended to the project's step 2: "For a question about the whole project, such as its features or what it does, read the opening section of each document whose outline covers a part of it, not only the first."
The rule: keep it if variant 9's `features` passes in at least 2 of 3 runs and no other case loses a pass that isn't noise. Measured with (c):

- `features`, variant 9: **0 of 3** (it called `read_document_pages` once, on the README, and cited one document each time); variant 50: 1 of 3, and `features-50` 0 of 3.
- `deploy-and-test`, variant 9: **3/3 became 0/3** (the model read pages and skipped the search, so `TESTING.md` was never reached: fact missing, chip for `TESTING.md` missing), variant 50: 1/3. Without (c) the same
  case is 3/3 in both variants, so this is not noise.

(c) is **removed**: its code, its golden lines and its test. The shipped runs above are without it. The cause of the `features` shortfall is unchanged, and a prompt sentence did not fix it.

### The RHEL question's answer shape

Spec section 2: say the documents don't cover RHEL; give the documents' own requirements, cited; add general knowledge introduced as not from the documents, with no page; end with the offer to search the web.
The "general knowledge, introduced as such" column is my reading of the answers, not a score.

| runs (shipped tree) | text and card | says the documents don't cover it | cites the documents' own requirements | general knowledge, introduced as such | offer in words | invented page |
|---|---|---|---|---|---|---|
| variant 9, 3 runs | **3/3** | 3/3 | 3/3 | 2/3 (the third says what to install without the label) | 0 | 0 |
| variant 9 diagnostic, 6 runs | 5/6 | 6/6 | 6/6 | 3/6 labelled; the rest say "you will likely need" | 0 | 0 |
| variant 50, 3 runs | **0/3** | 3/3 | 3/3 | 0/3 (the answers stay inside the documents: the container image, podman, nginx) | 0 | 1 of 3 |

Of the six runs of the brief (three of each variant) that is **text and card in 3 of 6**: all of variant 9's, none of variant 50's. In variant 50 the model answers from the documents (it finds the container
requirements in DEPLOYMENT.md) and ends without an offer, in words or by the tool. That is a consent-safe failure (no dead end), but not the shape section 2 asks for.

The eval scores some of these `gap-general` turns FAIL that it should not: its refusal wording (`doc_qa._REFUSAL`) has "not cover, mention, say, contain, include, ..." but not "list", and the model's usual first
sentence is "The project documents do not list specific RHEL 9 packages". With "list" accepted (my arithmetic, not a score the eval gives; the eval was not changed) variant 9 would be 3/3 in both runs and variant 50
2/3 in both (the failing turn is the invented page).

### Where this leaves the features question

"What are the features?" still fails: 1 turn of 9 across the two variants (`features` 1 of 3 in variant 50, 0 of 3 in variant 9, `features-50` 0 of 3). The answer is a long README list with a line or two from USER_GUIDE.md;
the model never reaches `LIBRARY.md`, `IDENTITY_AND_ROLES.md` or the admin and project areas, because nothing it has seen points there, and a rule sentence made the case worse. Untried: an overview the app builds
(each document's opening, which the project map already carries for small projects), one search per area name, or following the README's own "see also" links. Not done here.

## Rebuild timing

What an existing library pays, the first time a project chat opens after the upgrade: the background re-chunk of every document
under the new chunker version (`split1:1200:200` before; `split1:1200:200:code1:cells1` now), and how much of it is embedding.
CPU-bound: the embeddings are the local Ollama `nomic-embed-text` on this machine's CPU (the GPU is disabled), so the seconds
are for this machine and nothing else. The machine was also busy (load average 5.6-7.1 during the rebuilds: the app walk's
Chromium and another agent), so read the seconds as an upper estimate.

### How it was set up

- A fresh scratch database `natural_reader_rebuild` (created, used, **dropped at the end**; `natural_reader_eval` left in place).
  Migrations: the current ones, through `init_db`, as the eval script does.
- The frozen fixture (the nine documents of `server/evals/fixtures/project_docs`) seeded as a project with the eval's own `seed_project`.
- **The old index was made by the old code, not by editing a string.** The brief said to index the fixture and then set the stored
  chunker version to the old value. That edit alone does nothing useful: the stored chunks were just made by the *current*
  extractor, so their texts already equal the new ones, every vector is reusable, and the rebuild embeds nothing (I ran exactly
  that as the control below: 0 texts embedded, 0.5 s). So the nine documents were indexed with the **v2.3.0 extractor and pipeline**
  (the released code before the code-block and table-cell changes; `git archive v2.3.0 server`, put on `PYTHONPATH`, outside the
  repository; its `CHUNKER_VERSION` is `split1:1200:200`). Their stored profile is
  `nomic-embed-text|'search_document: '|'search_query: '|split1:1200:200` (it lives in `documents.embedding_profile`; the chunker
  version is its last `|` part, built from `extract.CHUNKER_VERSION` by `embeddings.current_profile()`). One departure from a real
  upgrade: the current migrations were applied before the old code indexed, not after; the old code ran without complaint.
- The upgrade was triggered the way a project chat triggers it: `chat.project_scope.load_project_scope(...)`, which calls
  `doc_pipeline.ensure_current` for each document of the project (the orchestrator calls it for every project turn). That only
  *schedules* one background rebuild per document; the script then waits for those tasks and times them. The rebuild is
  `doc_pipeline.run_rebuild`, one document at a time (a semaphore of 1), re-extracting the stored file and embedding only texts that
  have no vector yet in that document's old index.

### Result (run 1; run 2 had the same counts)

| document | old chunks | new chunks | embedded | reused | embedded, by kind | finished at |
|---|---|---|---|---|---|---|
| ARCHITECTURE.md | 92 | 92 | 3 | 89 | 3 table | 0.6 s |
| CHAT_WITH_PDF.md | 126 | 139 | 24 | 115 | 12 code, 12 table | 2.6 s |
| DEPLOYMENT.md | 84 | 88 | 8 | 80 | 4 code, 4 table | 3.4 s |
| DOCS_INDEX.md | 7 | 7 | 3 | 4 | 3 table | 3.8 s |
| IDENTITY_AND_ROLES.md | 56 | 57 | 7 | 50 | 1 code, 6 table | 4.8 s |
| LIBRARY.md | 105 | 107 | 15 | 92 | 2 code, 13 table | 6.8 s |
| README.md | 176 | 207 | 48 | 156 | 31 code, 17 table | 12.0 s |
| TESTING.md | 64 | 65 | 23 | 42 | 1 code, 22 table | 14.9 s |
| USER_GUIDE.md | 83 | 84 | 5 | 79 | 1 code, 4 table | 15.2 s |
| **total** | **793** | **846** | **136** | **707** | **52 code, 84 table** | **15.2 s** |

- **Total time: 15.2 s** (run 1) and **15.0 s** (run 2, a second fresh database; identical counts). Both are the wall clock from
  the call that opened the project to the last swap. The turn itself waited **5 ms** and **2 ms** (the scope load that schedules the
  rebuilds): the upgrade never blocks the chat.
- **136 of 843 distinct new chunk texts were embedded (16%); 707 (84%) kept their vector.** "Reused" is the distinct new texts minus
  the embedded ones (843 distinct texts in 846 chunks: README.md holds 3 duplicates). The pipeline's own log line says "N vectors
  kept"; that is the size of the document's *old* index, not how many the new set reused (e.g. ARCHITECTURE.md: 92 kept, 89 reused
  plus 3 embedded), so I counted from the stored texts instead and checked it against the "texts embedded" figure in the same log lines.
- Embedding: 13 calls (batches), 136 texts, 14.4 s (run 1) and 14.1 s (run 2) inside `embed_documents`, about 0.1 s per text. The
  rest of the 15 s is re-extraction, the reads and the swap transactions (the control below, which embeds nothing, takes about 0.05 s per document).
- **What was embedded.** Not only code chunks, as the brief expected (it also expected the table chunks): **52 code chunks** (42
  `code`, 10 `code+cont`; the extractor did not index code blocks before) and **84 table chunks** (66 `block`, 18 `block+cont`; the
  `cells1` change puts a space between a table's cells, so their text changed). I checked the 84 by re-extracting the fixture with
  both extractors outside the database: 64 of them are the same words as an old chunk with the cell separators now apart; the other
  20 start with a table header or row (`Symptom Cause Fix ...`, `Route Guard Notes ...`, `# Doc Audience What it owns ...`,
  `Endpoint Method Description ...`, `File Covers ...`) or continue a split table, where the longer text moved the split point. I judged
  that last group from the first ~110 characters of each; it is not machine-checked. No prose chunk was re-embedded.
- For scale, indexing the same nine documents from nothing with the old code took **51.9 s** and **51.7 s** (793 chunks embedded,
  extraction included): the lazy upgrade costs about 29% of the time and 17% of the embeddings of a full re-index.

### The control: the profile string edited, chunk texts left alone

After the run above the documents were current. Setting `embedding_profile` back to the old value
(`replace(embedding_profile, ':code1:cells1', '')`, the brief's setup edit) and opening the project again gave: 9 rebuilds, **0 texts
embedded, 843 vectors reused, 0.5 s** in total, 2 ms for the turn. That is what a profile-only edit measures: the re-chunk and
the swap, with no embedding. It is not the upgrade cost; the table above is.

### What it means, and what I did not measure

- A library of nine documents pays about 15 s of background work once, on first use, on this CPU; a turn waits for none of it.
- During that window the old chunks keep answering: the documents stay `indexed` and the embedding model is the same, so
  `ensure_current` does not mark them `reindexing` (read from `doc_pipeline.ensure_current`, not tested here). The old index lacks the code
  chunks and the separated table cells, so questions about those would be answered worse until the swap of the document concerned.
- Not measured: a project of many documents (the rebuild is serial, one gate for the whole process, so N documents queue; time
  scales with the changed chunks, roughly 0.1 s each here), rebuilds started from several users at once, and a GPU or a hosted
  embedding model. Also not measured: the answer quality of a turn that arrives mid-rebuild.
