# Handoff: v2.1 follow-ups, mid-plan (2026-09-29)

Read this first if you're a new session continuing this work, local or cloud. It is self-contained. The local SDD workspace (`.superpowers/`) is git-ignored and didn't come with the clone, so the files in this folder are its tracked copy.

## Where things stand (updated by the cloud session, 2026-09-29)

- **The plan:** [`docs/superpowers/plans/2026-09-28-v2.1-follow-ups.md`](../../plans/2026-09-28-v2.1-follow-ups.md), 10 tasks.
- **Branch:** the cloud session worked on `claude/task-mwo2ow` (from `development` at `ed3e90c`); the user merges it into `development`.

| Task | State |
|---|---|
| 1–3 | ✅ Done before this session |
| 4 · Local library belongs to the signed-in user | ✅ Reviewed, 1 fix round |
| 5 · Open a Library document | ✅ Reviewed, 1 fix round |
| 6 · Clickable citations | ✅ Reviewed, 1 fix round |
| 7 · Reader console errors | ✅ Approved (blob: cause fixed but not reproduced) |
| 8 · useAuth lint error | ✅ Approved; eslint clean |
| 9 · Deferred minors and stale docs | ✅ Approved |
| 10 · Provider errors; OpenRouter verified | ✅ Reviewed, 1 fix round (403 ruling: see ledger) |
| Final whole-branch review | ✅ Done; one fix pass (see ledger) |

**v2.2 — every piece of browser state belongs to its user** ([plan](../../plans/2026-09-29-v2.2-per-user-browser-state.md)), same branch:

| Task | State |
|---|---|
| A · Browser-only chats, workspace, reading positions per user | ✅ Reviewed, 2 fix rounds |
| B · Read-aloud never plays the previous page's clip | ✅ Approved |
| C · The open document's id is always its own bytes' hash | ✅ Approved |
| Final review | ✅ One fix (the unsent chat draft is per user too), re-reviewed clean |

**Suites at the branch head:** backend 610 passed, frontend 412 passed (vitest exit 0), `npx eslint src` clean, `vite build` OK.

## What is left

1. **The running-app walk on the local machine** (Ollama, real Kokoro, Keycloak):
   - two users on one browser: the library, browser-only chats, the workspace folder, reading positions and the chat draft;
   - Open from the Library as a real share recipient;
   - `llama3.2:3b` document questions;
   - read aloud, then reopen a PDF (`blob:`), and a page turn during read-aloud.
2. **Decisions for the user:**
   - the Task 10 403 wording;
   - ruling R2 revised (no `"local"` owner).
3. **Next plan (proposed): v2.3, model-provider independence.** Document embeddings are the one hard Ollama dependency (`server/services/embeddings.py` calls `/api/embeddings`). The plan:
   - an embeddings provider setting (OpenAI-compatible `/v1/embeddings`, e.g. vLLM);
   - a guard against mixing embedding models;
   - a re-embed path.

   Chat, summaries and Docling already work without Ollama.
4. **Then A0,** the project-management screen (sharing and members are still API-only).

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
