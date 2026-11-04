# Docs index — read in this order

Everything about the project, organized by audience, current as of `v2.8.0` plus `[Unreleased]` on `development`.
If you're touching a feature, its "owner" doc below is the first thing to read.

| # | Doc | Audience | What it owns |
|---|---|---|---|
| 1 | [USER_GUIDE.md](USER_GUIDE.md) | **End users** | Signing in, account states, access tokens, the Library, projects and people, versions, chat (one document or a whole project, citations, the web search card, attaching a text file, speaking a message, Speech language, Talk), Settings and profile, the admin section, daily token budgets, FAQ |
| 2 | [../README.md](../README.md) | **Users + operators** | Feature tour, tech stack, getting started (all `startup.sh` modes), the API tables, threat model, reverse-proxy/nginx + hardening recipes |
| 3 | [../deploy/README.md](../deploy/README.md) | **Operators (dev rig)** | The local Keycloak OIDC rig end-to-end: containers, env vars, the two-user walkthrough, prod-like nginx variant |
| 4 | [DEPLOYMENT.md](DEPLOYMENT.md) | **Operators (production)** | Serving on real hostnames behind nginx + TLS: the same-origin topology, the two vhost samples, the five config values that must agree, the traps (KC proxy headers, live-realm ≠ export, COOKIE_SECURE, upload body size, stale QUIC), voice input (settings, cost, models, the proxy), the speech window behind Talk, and optional Google Cloud speech (setup, the two recognizers that work, measured speed) |
| 5 | [ARCHITECTURE.md](ARCHITECTURE.md) | **Developers** | The whole system map: same-origin rule, frontend hooks/components, backend wiring/auth/routers/services, migrations, end-to-end flows, invariants & traps |
| 6 | [IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md) | **Developers** | How Keycloak users map to app users: the two stores, the `(iss, sub)` join key, JIT provisioning branches, capabilities (Keycloak realm roles, mirrored into `users.capabilities`, enforced by `require_capability`), the `natural-reader-admin` service account, credentials, create/delete propagation rules (live-verified) |
| 7 | [LIBRARY.md](LIBRARY.md) | **Developers** | The document library: content, entries and placements, the `can_read` access model (404-indistinguishable), sharing, projects, versions, the schema migrations, the docs + projects API surface and the Library view |
| 7a | [CHAT_WITH_PDF.md](CHAT_WITH_PDF.md) | **Users + developers** | Document and project chat end to end: indexing, search, the tools, the reader, web search consent, citations, prerequisites |
| 8 | [TESTING.md](TESTING.md) | **Developers** | The test map: how to run the suites safely, which file owns which feature, conventions (ASGITransport, MockTransport, `db_conn`), the manual/live verification ledger, known gaps |
| 9 | [superpowers/specs/](superpowers/specs/) | **Developers (design)** | Design specs, dated, oldest first: from the read-aloud extension (2026-07-27) through multi-user auth, the library, projects and roles, the chat orchestrator, the backend container and document versions, to project chat (2026-10-18), admin console v2 (2026-10-25), the message box inputs (2026-10-27), speech model choice and the speech window (2026-11-02) and Google speech (2026-11-03) |
| 10 | [superpowers/plans/](superpowers/plans/) | **Developers (execution)** | The committed TDD task plans that implemented the specs |
| 11 | [../CHANGELOG.md](../CHANGELOG.md) | Everyone | Every release, newest first; `[Unreleased]` is what's on `development` since the last tag. Per-release notes: [Release_notes/](Release_notes/) |
| 12 | [superpowers/handoff/](superpowers/handoff/) | Session continuity | One folder per feature build: what shipped, how it was verified (walks, evals), rulings, what's deferred. Newest: [project chat](superpowers/handoff/2026-10-18-project-chat/HANDOFF.md) |
| 13 | [../HANDOVER.md](../HANDOVER.md) | Session continuity | Session notes, newest on top; the top entry is where a fresh session starts, with the procedure and the recommended order of work |

Quick routing:

- "Why can't this user log in?" → USER_GUIDE (FAQ) then IDENTITY_AND_ROLES;
  if it's a *deployment* symptom (redirect_uri error, mixed content, cookie never
  sticks) → DEPLOYMENT.md § traps.
- "How do I deploy to real hostnames with TLS?" → DEPLOYMENT.md.
- "What env vars does the server take?" → `../.env.example` (annotated) then
  DEPLOYMENT.md § Model providers.
- "How do I let Google hear and speak?" → DEPLOYMENT.md § Google Cloud speech;
  who may use Talk → IDENTITY_AND_ROLES.md § The `talk` capability.
- "Which tests guard this change?" → TESTING.md.
- "How do I remove a user properly?" → USER_GUIDE § Admin (offboarding order).
- "Who can read this document / how is sharing decided?" → LIBRARY.md
  (`can_read`: an entry you hold or a project you belong to, 404-indistinguishable).
- "Why did the assistant answer that way / search the web?" → CHAT_WITH_PDF.md,
  then the project-chat HANDOFF (known answer-quality gaps).
- "Where's the next work?" → the top entry of HANDOVER.md.
