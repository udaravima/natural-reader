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
