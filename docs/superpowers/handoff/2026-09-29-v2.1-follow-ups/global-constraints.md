# Global constraints (every task)

Project: Natural Reader — FastAPI + psycopg 3 + Postgres (pgvector) backend in `server/`, React 19 + Vite SPA in `src/`. Repo: /home/fire310w/Documents/Github/natural-reader, branch `development`.

- Work on `development`. Never switch branches, never push, never commit. **Stage** your changes (`git add <exact files>`) and stop — the controller reviews the staged diff and commits.
- Do not dispatch subagents.
- Nothing deployment-specific hardcoded: new settings are env vars with safe defaults plus a commented line in `.env.example`. Docs use example.com.
- API keys never in logs, events or responses. No user content (message text, queries, document text) at WARNING or above.
- Any document read route returns 404 (never 403) to a caller who can't read the document — use the existing `can_read` SQL predicate helpers in `server/auth/authz.py`.
- Every task leaves both suites green: `.venv/bin/python -m pytest server/tests -q` (Postgres container is up) and `npx vitest run`. `npx eslint src` must be clean (0 problems) — Task 8 removed the last error.
- TDD: write the failing test first, watch it fail for the right reason, then implement.
- Every user-visible change gets one CHANGELOG `[Unreleased]` line (plain, bold lead, matching the file's voice; create `### Added/Fixed/Changed` under `## [Unreleased]` as needed) and a correction to any doc that described the old behaviour.
- Match the surrounding code's style, naming and comment density.
- Servers: a backend may be listening on 127.0.0.1:8000 — leave it alone; don't bind 8000/5173.
- Podman from this shell needs `env -u XDG_DATA_HOME podman ...`.
