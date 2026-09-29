# Handoff: v2.1 follow-ups, mid-plan (2026-09-29)

Read this first if you're a new session continuing this work, local or cloud. It is self-contained. The local SDD workspace (`.superpowers/`) is git-ignored and didn't come with the clone, so the files in this folder are its tracked copy.

## Where things stand

- **The plan:** [`docs/superpowers/plans/2026-09-28-v2.1-follow-ups.md`](../../plans/2026-09-28-v2.1-follow-ups.md), 10 tasks. v2.1.0 is already released; this is the work after it.
- **Branch:** `development`. The last commit before this handoff is `04a17bb` (Task 4).

| Task | State | Commits |
|---|---|---|
| 1 · Web-search fetch caps | ✅ Done, reviewed clean | `b02343e` |
| 2 · Recover tool calls written as text | ✅ Done, one fix round, reviewed clean | `8bcc312`, `07ee9e5` |
| 3 · Index button follows the document on first open | ✅ Done, reviewed clean | `cf09c24` |
| 4 · Local library belongs to the signed-in user | ⏳ Implemented, **not reviewed yet** | `04a17bb` |
| 5 · Open a Library document (`GET /v1/docs/{id}/file` and an Open button) | ⬜ | |
| 6 · Clickable citations | ⬜ | |
| 7 · Reader console errors | ⬜ | |
| 8 · The `useAuth.js` lint error (now at line ~63) | ⬜ | |
| 9 · Deferred minors and stale docs | ⬜ | |
| 10 · Provider errors say what happened; OpenRouter verified | ⬜ | |

**Suites at `04a17bb`:**
- backend: 579 passed;
- frontend: 321 passed;
- `npx eslint src`: exactly 1 error, the old one in `useAuth.js`, which Task 8 removes.

## What to do next, in order

1. **Review Task 4** (range `07ee9e5..04a17bb`).
   - Use `reviewer-template.md` in this folder, filling in:
     - `task-4-brief.md` (the brief);
     - `global-constraints.md`;
     - `task-4-report.md`;
     - a diff of the range.
   - Things to look at hard:
     - the IndexedDB v4→v5 migration, which changes the key scheme to `ownerId\0fileName`, and whether any existing record could be lost;
     - claiming legacy records once;
     - the per-owner cap on recent books;
     - `useAuth` calling `setLibraryOwner`.
   - Fix what it finds, then run a scoped re-review with `re-review-template.md`.
2. **Tasks 5 to 10, one at a time.** Implement, then review, then fix rounds (at most 5), then the next task.
   - Take each task's text from the plan file.
   - Only one implementer may work in the tree at a time; see the ledger for what happened when two did.
3. **Final whole-branch review** of `7dc0987..HEAD`, on the most capable model. Point it at the deferred minors in `ledger.md`. Then one fix pass and one re-review.
4. **Walk the changes in the running app:**
   - the Index button on a new Markdown file;
   - two users on one browser (Task 4);
   - Open from the Library as a share recipient (Task 5);
   - clicking a citation (Task 6);
   - `llama3.2:3b` document questions (Task 2).

   A running-app walk needs Ollama and the models, so this step belongs to the local machine, not a cloud sandbox.

## Rules that bind this work (from the user)

- **Commits:** commit as you go on this plan (user-approved 2026-09-29).
- **Pushing and merging:** don't push or merge without asking the user.
  - A cloud session that has to push will push to its own branch.
  - Say which branch in the summary, and let the user merge into `development`.
- **Deployment-specific values:** nothing deployment-specific is hardcoded. New settings are env vars with safe defaults, and docs use example.com.
- **Secrets:**
  - API keys never appear in logs, events or responses.
  - Never print the value of `.env` keys. The user's OpenRouter key sits in `.env` as `OPENROUTER_API_KEY`; the app reads `INFERENCE_OPENROUTER_API_KEY`.
- **User content:** none at WARNING or above.
- **Before calling anything done, answer:** "Can the user do it start to finish without curl?" Say plainly what is still API-only.
- **How to explain:** plain language, cause-and-effect chains, stakes, what was verified versus assumed.

## Rulings already made

All rulings are in `ledger.md`, with their reasons. The ones later tasks depend on:
- **R1:** the library owner is module state in `src/db.js` (`setLibraryOwner(id)`), and the signatures of `saveBook` and `getBook` stay unchanged.
- **R2:** the owner id is `/v1/auth/me`'s `id`; `"local"` is used only when that call fails.
- **R4 (Task 6):**
  - Check whether saved replies carry the document's id.
  - If they don't, the server adds `docId` to the saved prefetch note and to the `search_document` tool summary.
- **Task 2 fix:** once a provider remembers that a model rejected tools, `capabilities()` reports `tools=False` (`FeatureMemory.apply` in `server/llm/providers/base.py`).
- **Parallel reviews:** task reviews may run alongside the next implementer. Two implementers may never run at once.

## Environment for a cloud or fresh machine

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm ci
npx vitest run            # frontend: no services needed
npx eslint src
```

**Backend tests** need Postgres 16 with the **pgvector** extension, found via `DATABASE_URL`. The default is `postgresql://natural_reader:natural_reader@localhost:5433/natural_reader`.

- With Docker or Podman: `docker compose up -d postgres` (image `pgvector/pgvector:pg16`, port 5433).
- Without a container runtime, a Debian or Ubuntu sandbox can install the packages:
  ```bash
  sudo apt-get install -y postgresql-16 postgresql-16-pgvector
  sudo -u postgres psql -c "CREATE ROLE natural_reader LOGIN PASSWORD 'natural_reader' SUPERUSER;"
  sudo -u postgres createdb -O natural_reader natural_reader
  export DATABASE_URL=postgresql://natural_reader:natural_reader@localhost:5432/natural_reader
  .venv/bin/python -m pytest server/tests -q
  ```

  Migrations run automatically. If no Postgres can be had, say so. Run the frontend suite plus the backend tests that don't touch the pool, and leave the full backend run to the local machine.

**Not needed for tests:**
- Ollama;
- SearXNG;
- Keycloak;
- `.env`.

## Facts checked 2026-09-29 (useful for Task 10)

- **OpenRouter through the OpenAI-compatible adapter works.** It was run with paid `google/gemma-4-26b-a4b-it` and `mistralai/mistral-small-3.2-24b-instruct`:
  - a three-turn chat;
  - two pins over two turns;
  - a native `web_search` tool round;
  - an image and a follow-up question;
  - usage counts reported by the provider;
  - no API key in the logs.
- **Free `:free` models returned 429.** The raw error said "temporarily rate-limited upstream", but the user saw "The model provider returned an error: Provider returned error". That is Task 10.
