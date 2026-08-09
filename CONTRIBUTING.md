# Contributing to Natural Reader

Thanks for your interest in contributing! Natural Reader is a local-first
document reader with integrated Kokoro TTS, an Ollama-backed chat/RAG layer, and
a Chrome read-aloud extension. This guide covers how to get set up, the quality
bar, and how to propose changes.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).

## Ways to contribute

- **Bugs / features:** open an issue describing the problem or proposal before a
  large change, so we can agree on direction first.
- **Docs:** fixes to the README, this guide, or the release notes are always welcome.
- **Code:** see the workflow below.

## Prerequisites

- **Node.js** `^20.19` or `>=22.12` (Vite 7 / Rolldown) and **npm**
- **Python** `>=3.10, <4.0` (Docling and the doc-chat routes use 3.10+ unions)
- **Docker or Podman** (for Postgres + SearXNG)
- **Ollama** running locally, for the chat / embedding / web-search features

## Getting set up

The `startup.sh` script bootstraps everything:

```bash
./startup.sh init      # venv + Python deps, Kokoro models, npm install + build
./startup.sh up        # Postgres + SearXNG containers, then the backend (run.py)
```

`up` runs in the foreground; Ctrl-C stops the backend and the containers cleanly.
For frontend development with hot-reload, run the dev server in a second shell:

```bash
npm run dev
```

Copy `.env.example` to `.env` to override any defaults (DB URL, Ollama URL,
embedding model, `WEB_SEARCH_*`, logging, …). `.env` is gitignored.

## Running the tests

Every behavior change should come with tests, and both suites must pass:

```bash
# Frontend (Vitest + Testing Library)
npm run test:run

# Backend (pytest — hermetic; network is mocked with respx)
.venv/bin/pytest server/tests

# Lint the frontend
npm run lint
```

The backend logs to `logs/server.log` (rotating) and the console when running;
`logs/` is gitignored.

## Branching & pull requests

- The default branch is **`master`**. Fork the repo, branch from `master`
  (e.g. `feat/short-description`), and open your PR against `master`. The
  maintainer integrates ongoing work via the `development` branch.
- Keep PRs focused — one logical change per PR is much easier to review.
- Make sure `npm run test:run`, `.venv/bin/pytest server/tests`, and
  `npm run lint` are green before requesting review.
- Update `CHANGELOG.md` under the `[Unreleased]` heading for any user-facing change.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>

<body — the why, not just the what>
```

Common types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`.
Scopes seen in this repo include `chat`, `web-search`, `startup`, `gitignore`.
Example: `fix(web-search): validate before fetch; cap count at 10`.

## Larger features

For anything beyond a small fix, we sketch the design before coding. Design specs
and implementation plans live under [`docs/superpowers/`](docs/superpowers/) —
browse them to see the level of detail we aim for, and feel free to open a design
discussion in an issue first.

## Code style

- Match the surrounding code — naming, structure, and comment density.
- **Frontend:** React function components + hooks, Tailwind for styling. Keep
  files focused; pure logic goes in `src/hooks/` or `src/lib/` with unit tests.
- **Backend:** FastAPI with type hints; network-facing services live in
  `server/services/`, routers in `server/routers/`. Never trust remote input —
  e.g. the `web_search` fetcher is SSRF-guarded; keep that kind of guard intact.
- Secrets and machine-specific config belong in `.env` / gitignored files, never
  in the repo.

Thanks again — happy hacking!
