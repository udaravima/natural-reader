# Docs index — read in this order

Everything about the project, organized by audience. Post-`v1.9.0` work
(multi-user auth + the inference gateway) is fully covered by this set; if
you're touching a feature, its "owner" doc below is the first thing to read.

| # | Doc | Audience | What it owns |
|---|---|---|---|
| 1 | [USER_GUIDE.md](USER_GUIDE.md) | **End users** | Signing in, account states, personal access tokens (why + how), the admin section, Server/Local chat source, daily token budgets, FAQ |
| 2 | [../README.md](../README.md) | **Users + operators** | Feature tour, tech stack, getting started (all `startup.sh` modes), the Inference Gateway API table, threat model, reverse-proxy/nginx + hardening recipes |
| 3 | [../deploy/README.md](../deploy/README.md) | **Operators (dev rig)** | The local Keycloak OIDC rig end-to-end: containers, env vars, the two-user walkthrough, prod-like nginx variant |
| 4 | [ARCHITECTURE.md](ARCHITECTURE.md) | **Developers** | The whole system map: same-origin rule, frontend hooks/components, backend wiring/auth/routers/services, migrations, end-to-end flows, invariants & traps |
| 5 | [IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md) | **Developers** | How Keycloak users map to app users: the two stores, the `(iss, sub)` join key, JIT provisioning branches, roles (app-owned, not IdP claims), credentials, create/delete propagation rules (live-verified) |
| 6 | [LIBRARY.md](LIBRARY.md) | **Developers** | The document library (RAG Phase 0): the `can_read` access model (owner / project-member / grantee, 404-indistinguishable), migration 009 schema, the docs + projects API surface, the Library view + upload picker, admin reassignment, and capability coordination with the auth branch |
| 7 | [TESTING.md](TESTING.md) | **Developers** | The test map: every post-1.9.0 test case and what it asserts, conventions (ASGITransport, MockTransport, `db_conn`), the manual/live verification ledger, known gaps |
| 8 | [superpowers/specs/](superpowers/specs/) | **Developers (design)** | Design specs in order: multi-user auth (2026-09-13), SPA auth UI (2026-09-16), model-router gateway (2026-09-17), document-library RAG (2026-09-17), **admin console (2026-09-17 — approved, future branch `feat/admin-console`)** |
| 9 | [superpowers/plans/](superpowers/plans/) | **Developers (execution)** | The committed TDD task plans that implemented the specs |
| 10 | [../CHANGELOG.md](../CHANGELOG.md) | Everyone | `[Unreleased]` = everything since `v1.9.0`: auth + gateway entries |
| 11 | [../HANDOVER.md](../HANDOVER.md) | Session continuity | Newest-on-top session notes: what shipped, incidents, what's next |

Quick routing:

- "Why can't this user log in?" → USER_GUIDE (FAQ) then IDENTITY_AND_ROLES.
- "What env vars does the server take?" → `../.env.example` (annotated) then
  README § Inference Gateway.
- "Which tests guard this change?" → TESTING.md.
- "How do I remove a user properly?" → USER_GUIDE § Admin (offboarding order) —
  full delete tooling is specced in superpowers/specs (admin console).
- "Who can read this document / how is sharing decided?" → LIBRARY.md
  (`can_read`: owner / project-member / grantee, 404-indistinguishable).
- "Where's the next work?" → HANDOVER (top entry) + the admin-console spec.
