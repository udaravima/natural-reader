# SDD ledger — plan: docs/superpowers/plans/2026-09-28-v2.1-follow-ups.md

Spec: none (a fix plan; evidence = the C1 and A1 walk docs + C1 final review). Rulings are provisional against the plan text and those docs.
Branch: development (user: never push). Plan committed 7dc0987. Baseline: backend 552, frontend 304, eslint 1 error (useAuth.js:51).
Ruling: implementers stage only; the controller commits after checking the staged diff — user answered "Subagents, commit as you go" (2026-09-29) to the controller; implementers decline relayed consent — cost if wrong: none (same commits).

## Pre-flight scan

| Pair / task | Produces → consumes | Found |
|---|---|---|
| T3 ↔ T7 | both edit src/hooks/usePdfEngine.js (T3 loaders ~248-303, T7 render ~110) | different functions; T7 must keep T3's awaited saveBook ordering |
| T3 ↔ T4 | T3 awaits `saveBook(file, progress)`; T4 adds an owner to records | conflict risk if T4 changes saveBook's signature → Ruling R1 |
| T4 → T5 | T5 saves fetched bytes into the local library under the signed-in owner | consumes T4's owner mechanism (R1) |
| T5 → T6 | T6 opens a cited doc through T5's open-from-server path | T6 must reuse T5's helper, not duplicate the fetch |
| T4 ↔ T8 | T4 needs the signed-in user's id (useAuth/App); T8 edits useAuth.js:51 | T8 runs after T4; T8 must keep T4's owner wiring |
| T5 → T9 | T9 rewrites the README endpoint table / CHAT_WITH_PDF.md | must include T5's `GET /v1/docs/{id}/file` |
| T1 | web_search.py only | self-consistent |
| T2 | orchestrator step loop; tests with scripted provider | self-consistent; the hold must not delay non-`{` text (review focus 2) |
| T3 | "App-level test" may be impractical in this harness → Ruling R3 |
| T4 | "loopback bypass → 'local'" vs /v1/auth/me returning the seed admin under the bypass → Ruling R2 |
| T5 | filename from "the caller's own entry name, or the name the sharer gave" — the entry row carries the name the sharer gave for shares | self-consistent |
| T6 | assumes saved doc_context/tool summaries carry `docId` — unverified → Ruling R4 |
| T7 | blob URL item is an investigation | allowed; report root cause |
| T8 | eslint must be clean after T8; earlier tasks must not add lint errors | carried into every dispatch |
| T9 | self-consistent |

Ruling R1: T4 keeps `saveBook(file, progress)` and `getBook(name)` signatures; the owner is module state in src/db.js set by `setLibraryOwner(id)` from the auth layer — T3/T5 call sites stay untouched — cost if wrong: a module-level global instead of an explicit parameter (slightly less pure).
Ruling R2: the owner id is always `/v1/auth/me`'s `id` (the bypass returns the seed admin, so it exists); `"local"` only when /auth/me is unavailable — cost if wrong: a bypass-mode library tagged with the seed admin's id instead of "local" (harmless).
Ruling R3: T3's App-level test may be replaced by the narrowest test that pins "pdfFileName is not set before saveBook resolves" plus a hook/App test that the hash effect finds the bytes on first open, if a full App render is impractical — the behaviour, not the test layer, is the requirement — cost if wrong: less end-to-end coverage (the final walk re-checks it in the browser).
Ruling R4: T6's implementer verifies whether stored replies carry the document id; if not, the server adds `docId` to the saved prefetch note / search_document tool summary (it knows context.doc_id) — cost if wrong: one extra field in stored doc_context.
Ruling R5: model choice — T2 opus (streaming hold logic), T8 haiku (one-file lint fix), others sonnet; reviewers sonnet, T2 and T5 (authz route) reviewers opus — cost if wrong: an extra fix round.
Task 1: dispatched (sonnet) BASE=7dc0987
Task 1: implemented b02343e (backend 556); review dispatched (sonnet)
Ruling: run each task review in parallel with the next implementer (disjoint files; controller commits by pathspec) — saves ~15 min/task — cost if wrong: a fix round and the next task touch the same file (CHANGELOG) and need a manual split.
Task 2: dispatched (opus) BASE=b02343e
Task 1: complete (commits 7dc0987..b02343e, review clean)
Task 1: minor (deferred): byte cap checked after each chunk is appended (one large chunk can overshoot memory briefly; returned bytes are exact).
Task 1: minor (deferred): the "Non-2xx" log line now names the final redirect hop's URL, not the requested one.
Task 1: minor (deferred): one web_search call fetches up to 10 pages at concurrency 4, so the whole tool call can take ~3 × WEB_SEARCH_FETCH_TOTAL_S plus summaries; the per-page deadline is what the plan asked for.
OpenRouter check (user added a key, 2026-09-29): test backend :8001 with INFERENCE_OPENROUTER_* from env. Paid gemma-4-26b-a4b-it + mistral-small-3.2: 3-turn memory ✓, 2 pins over 2 turns ✓, native web_search tool round ✓ (after starting SearXNG), image + follow-up ✓, provider usage counts ✓, key absent from logs ✓. Free models: 429 "rate-limited upstream" → our message hides it ("Provider returned error").
Ruling: add Task 10 to the plan — provider 429/rate-limit and 401/402 errors get a message that says what happened, and the docs/release notes say OpenRouter is verified — found while checking the user's OpenRouter key; the plan's goal is fixing what users notice — cost if wrong: one extra small task.
Task 2: implemented 8bcc312 (backend 575); review dispatched (opus)
Task 2: note — implementer also accepts {"type":"function",...} (llama3.2 variant); held text of an aborted step is billed but not saved.
Task 3: dispatched (sonnet) BASE=8bcc312
Session limit hit 01:48; resumed 08:58. Task 2 review + Task 3 implementer re-dispatched (tree was clean).
Task 2 live check (llama3.2:3b, zephyr doc, 4 runs): 3 native search_document (correct), 1 web_search; 0 JSON-text replies, so recovery not exercised live — verified by tests only.
Task 2: review (opus) — Needs fixes: 1 Important (silent tools strip after a remembered rejection → orchestrator still recovers a tool never sent), 5 Minor.
Ruling: fix via capabilities() reporting tools=False once the feature memory holds a tools rejection (reviewer's preferred option) — keeps "offered" truthful for probe and prompt — cost if wrong: a model whose rejection was transient loses tools until the process restarts (same as today's memory).
Task 2: minor (deferred): hold continues after a complete leading JSON object closes and prose follows (per brief's first-char rule; could release on raw_decode).
Task 2: minor (deferred): no test crosses the 2000-char cap across chunks.
Task 2: minor (deferred): `_text_tool_call` parameter named `offered` though callers pass step tools.
Task 2: minor (deferred): test import order (server.services.doc_search between chat/llm imports).
Task 2: minor (deferred): held span lost to Stop/provider error is billed but not saved; CHANGELOG doesn't say so.
Task 2: fix round 1 dispatched (resume implementer) FIX_BASE=8bcc312
Task 2: ⚠️ (reviewer) unrecognised text shapes — JSON list, <|python_tag|> prefix, two calls, arguments as JSON string — not handled; logged for the final walk.
Controller error: ran Task 2's fix round and Task 3's implementer concurrently (two implementers in one tree) — Task 3's agent unstaged Task 2's files and staged a hand-built CHANGELOG blob. Nothing lost; split by path/hunk into cf09c24 (Task 3) and 07ee9e5 (Task 2 fix r1). Combined tree: backend 579, frontend 311, eslint 1 (baseline).
Ruling: from here on only one implementer at a time (fix rounds included); task reviews may still run alongside the next implementer — the skill forbids parallel implementers, reviewers are read-only — cost if wrong: slower wall-clock.
Task 2: fix round 1/5 implemented 07ee9e5; scoped re-review dispatched (sonnet)
Task 3: implemented cf09c24 (frontend 311; R3 path: ordering test on 3 loaders + narrow hook/effect test); review dispatched (sonnet)
Task 4: dispatched (sonnet) BASE=07ee9e5
Task 2: fix round 1/5 (1 addressed, 0 open; commits cf09c24..07ee9e5)
Task 2: complete (commits b02343e..07ee9e5, review clean after 1 fix round; 5 minors deferred)
Task 3: complete (commits 8bcc312..cf09c24, review clean)
Task 3: minor (deferred): hash-on-first-open test covers markdown/text, not the PDF branch (ordering test covers all three).
Task 3: minor (deferred): failure-path toast test covers PDF + markdown, not text (shared call site).
Task 3: minor (deferred): the two new test files duplicate db/pdfjs mock boilerplate.
Task 4: implemented 04a17bb (frontend 321; composite owner\0name keys, DB_VERSION 4→5 migration, per-owner LRU cap; delete/updateBookMeta scoped) — review NOT yet dispatched (handoff to cloud session)
Cloud session (2026-09-29, branch claude/task-mwo2ow): controller implements fixes and tasks itself (one implementer in the tree at any time), reviewers are subagents. Env: Postgres 16 + pgvector on 5433, Python 3.12 venv (3.11 lacks inspect.getasyncgenstate → 11 chat_turns failures), backend baseline 579.
Task 4: review — Needs fixes: 2 Important (the "local" fallback claimed legacy records; saves with no known owner went into the claimable bucket), 5 Minor.
Task 4: fix round 1/5 implemented 15e063a (claimLegacy flag, fail-closed on a null owner, v1 backfill folded into the rebuild, key fields after spreads, useAuth wiring test, CHANGELOG); re-review: all addressed, 1 new Minor (CHANGELOG "offline list" sentence) fixed in the next commit.
Task 4: complete (commits 07ee9e5..15e063a+changelog, review clean after 1 fix round)
Task 4: minor (deferred): tabs still running v4 code get a VersionError after the upgrade (saves return false until reload; no data lost).
Task 4: minor (deferred): the 'local' owner is unreachable in the app today (state 'error' shows AuthErrorScreen, not the reader) — decide in the final review whether R2's fallback stays.
Task 4: minor (deferred): the claim's collision skip is now effectively dead code (only the migration writes UNCLAIMED_OWNER).
Task 5: implemented 2b0b6f0 (backend 591, frontend 346); review (opus) — Needs fixes: 1 Important (file response browser-cacheable → bytes reach the next user of a shared browser without the read gate), 5 Minor.
Task 5: fix round 1/5 implemented 1188998 (Cache-Control private,no-store; stored type wins over name; forgetDocHash; open-then-switch; PDF-branch + 404-body tests); re-review: all addressed, 2 new Minor — forgetDocHash ran before the save landed (fixed in the next commit: moved into persistBook's finally, with an ordering test), no test of openServerDoc's failure toast (deferred).
Task 5: complete (commits d0f79fe..1188998 + forget-order fix, review clean after 1 fix round)
Task 5: minor (deferred): the served bytes aren't re-hashed against doc_id (upload is the only writer and checks it).
Task 5: minor (deferred): openServerDoc's failure path (toast, view unchanged) has no test — App isn't mounted in tests (R3).
Task 5: minor (deferred, pre-existing): App's currentDocId effect re-runs only when pdfFileName changes, so different bytes opened under the already-open name keep the old doc's hash for the Index button (and Task 6's "already open" check) until the name changes.
Task 5: note — a 409 tells a share recipient "Upload the file again first", which only helps if they have the file.
Task 6: R4 check — the prefetch note already carried docId; the search_document summary did not → server adds docId/docName to it (Tool.summarize gets ctx); never sent to the model.
Task 6: implemented 4018753 (frontend 374); review — Needs fixes: 1 Important (unstable onOpenCitation in memo deps → every streaming token remounted every reply's markdown), 6 Minor.
Task 6: fix round 1/5 implemented 0f05193 (handler via ref, primitive memo deps, page 0 stays text, saved sentence only on the saved page, USER_GUIDE, 409 + #page-link tests); re-review: all addressed, no new breakage.
Task 6: complete (commits b56d571..4018753 + 0f05193, review clean after 1 fix round)
Task 6: minor (deferred): "page N" in a reply that used both web_search and the document always opens the document (per brief) — check in the walk.
Task 6: minor (deferred): the App wiring (openDocDeps, openDocId from currentDocId) isn't mounted in tests (R3) — walk: click a citation with the doc open, not open, and after a revoke.
Task 6: note — replies saved before this change have no docId in search_document summaries; their citations link only if the reply also had a prefetch note.
Task 7: implemented 6bd759d + 8cacf09 (changelog); running-app check in headless Chromium: canvas error and nested-button warning reproduce on the old code and are gone; the blob: error did not reproduce (old or new) — candidate root cause fixed (reader <audio> src revoked while loaded).
Task 7: review — Approved; 4 Minor: 3 fixed in the next commit (seq check after getTextContent + test, test comment, unconfirmed-cause comments reworded).
Task 7: complete (commits 4018753..8cacf09 + minors commit, review Approved)
Task 7: minor (deferred): the recent-book row is role="button" containing a <button> (axe nested-interactive), as the brief asked; holding Enter repeats open.
Task 7: minor (deferred, pre-existing): a TTS fetch in flight when clearCache runs lands in the new page's cache at the same index, so the new page could play the old page's clip there.
Task 7: walk — confirm the blob: error is gone with real Kokoro TTS (reopen a PDF during and after read-aloud).
Task 8: implemented 4b1654d (eslint clean — readMe() + apply in .then; refresh keeps the loading gate); review — Approved, 3 Minor: host-change keeps previous state (disclosed), stale guard now covers setLibraryOwner and refresh via one probeSeq (fixed in the next commit, 2 tests), weak test kept.
Task 8: complete (commits a5a81ac..4b1654d + follow-up, review Approved)
Task 9: implemented a94d30c (backend 597: /api/show DEBUG test (already implemented by df6f253), "local:" refused, recover_stale counts claims, drain_background loop; CHAT_WITH_PDF/README/ARCHITECTURE docs); review dispatched.
