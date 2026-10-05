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
Task 9: review — Approved; 3 Minor: 2 overstated doc sentences (re-index search gap; "indexed at once" only when already indexed) fixed in the next commit; the Ollama DEBUG test is a regression pin (never red — already implemented by df6f253, which is on development).
Task 9: complete (commits 4b1654d..a94d30c + doc fix, review Approved)
Task 10: implemented 66c878e (backend 607); live OpenRouter check with the user's temporary key (never printed/committed): free models 429 → the new message with "temporarily rate-limited upstream"; paid mistral-small 2 turns with memory + provider usage; 5 refused turns not charged (inference_usage = exactly the 2 paid turns); wrong key → 401 explained; no key in logs.
Task 10: review — Needs fixes: 1 Important (plan-mandated 403 → "credentials" is wrong for OpenRouter moderation refusals), 6 Minor.
Ruling: deviate from the plan — 403 → "The provider refused this request." + the provider's reason; only 401 is credentials — found by the Task 10 review (OpenRouter's 403 = moderation-flagged input); flagged to the user in the summary — cost if wrong: a provider that uses 403 for a bad key shows "refused" plus its own text instead of the admin hint.
Task 10: fix round 1/5 implemented 7234a80 (403 refused+reason, Ollama /api/tags mapped, trim/redact pin, /models test, billing wording, Ollama /v1 in the verified list); re-review dispatched.
Task 10: note — OpenRouter's metadata.raw for free models says "add your own key", appended verbatim per the brief.
Cloud walk (headless Chromium, dev bypass, backend on OpenRouter, TTS stubbed; not a substitute for the local walk): Library → Open of a server doc → reader shows it, GET /file 200 "private, no-store", Index button reads Indexed (hash = doc id); citation (page 3) with the doc open → page 3/3; citation (page 2) in a fresh profile → fetched and opened at page 2; citation to an unreadable doc → toast "Could not open "Gone.pdf": This document doesn't exist or you don't have access.", stays in chat; reopen/reload walks: console clean.
Still for the local walk: two users on one browser (Task 4); Open as a real share recipient (Task 5); llama3.2:3b document questions (Task 2); read-aloud + reopen with real Kokoro (Task 7 blob:).
Task 10: fix round 1 re-review — all addressed; 3 cosmetic minors (403 detail marker/JSON fallback, EOF blank line) folded into the final fix pass.
Task 10: complete (commits 3fddf97..7234a80, review clean after 1 fix round; 403 ruling for the user to accept)
Final review (opus, 7dc0987..664590c): Needs fixes — 2 Important: (1) `npx vitest run` exited 1 (unhandled rejection in usePdfEngine.openServerDoc.test.js since 1188998 — the controller had been reading pass counts, not the exit code); (2) currentDocId followed the file name, so a same-name open (Library/citation) kept the old doc's id for chat and the "already open" check. Plus: R2 "local" fallback → null (fix now); minors: extractPageText seq guard, vacuous 700 ms timer tests, drain-test cleanup, PEP 8, nosniff, recovered-call docId assertion; ⚠️ other local stores (legacy IDB chats, last workspace, reading progress) are still shared per browser — follow-up task.
Ruling (revises R2): when /v1/auth/me fails the library owner is null, not "local" — final review: a shared bucket contradicts review focus 4 and the path is unreachable today — cost if wrong: none visible (the error state shows AuthErrorScreen).
Final fix pass: vitest exit 0 (fresh rejected promise per call); docLoadId in usePdfEngine + currentDocId derived from {loadId, docId} (no setState in the effect); owner null on /me failure; extractPageText seq guard; fake-timer tests proven red without the prevPageRef fix; drain test gathers the cancelled task; nosniff on /file; recovered call's summary carries docId (test); 403 detail = the provider's error.message only, trimmed with a marker; e.repeat ignored on the welcome row. Suites: backend 610, frontend 391 (exit 0), eslint clean. Cloud browser check: a local "Walk Paper.pdf" open, then a citation to the server's "Walk Paper.pdf" → GET /file 200 and the server doc's page 3 shown.
Final fix pass re-review (opus): all 10 findings addressed, no new Critical/Important; 1 new Minor (EOF blank line in test_llm_openai.py) fixed in the last commit.
Final: minor (deferred, pre-existing): docHash's name-keyed cache — a hash still in flight for load N can repopulate the cache after load N+1's forgetDocHash when both files have the same size.
Final: minor (deferred, pre-existing): a workspace file isn't saved locally, so ensureDocHash hashes a same-named library book instead.
Branch done: claude/task-mwo2ow — backend 610, frontend 391 (exit 0), eslint clean. Left: the local running-app walk; the "other local stores are per-browser" follow-up task.
v2.2 (plan docs/superpowers/plans/2026-09-29-v2.2-per-user-browser-state.md): Task A 7a70e4e (review: Needs fixes — the restore gate was untested; fix round b142b49, re-review dispatched); Task B 52b2b9c + Task C 69cde96 (review: both Approved; minors applied next: Index on a folder file says why it can't, clearDocHashCache bumps generations, test globals restored, EOF blank lines).
v2.2 Task B/C: minor (deferred): the staleFetch test uses two 20 ms real waits; App's docInLibrary guard has no App-level test (R3).
v2.2 Task A: fix round 2/5 1cfc767 (owned state via visibleTo) — re-review: all addressed; 1 new Minor (a late restore for the previous user could overwrite, not reveal, the new user's state) fixed in the next commit with a signed-in-user ref; adoptWorkspace also clears a pending reconnect banner.
v2.2 Task A: complete (review clean after 2 fix rounds)
v2.2 Task A: minor (deferred): hiding the workspace doesn't unload a document already opened from the previous user's folder into the reader (a user change without a reload only happens via an API host change; logout reloads).
v2.2 final review (opus, f55206f..1022e22): Needs fixes — 1 Important: the unsent chat draft (`neural-pdf-chatDraft`) was still shared per browser and read/written with no user known. Fixed: useUserDraft(signedInUserId) stores it at `neural-pdf-chatDraft@<userId>`, none with no user, legacy key claimed at first sign-in (test written together with the hook, not red-first); cloud browser check: per-user key, survives a reload. Minors (deferred): the claim runs on every /me (idempotent; a "done" marker would save the full read); localStorage part of the claim isn't atomic across tabs; on a browser several users used under v2.1, the v2.1-era chats/workspace/positions go to the first sign-in after upgrading (release note if v2.1 shipped separately); 20 ms real waits in staleFetch test.
v2.3 (C2 slice 1, plan docs/superpowers/plans/2026-09-29-v2.3-document-qa.md): written after a prompt audit and aligned with the C2 roadmap (search_documents name, scope-based internals, per-tool guidance, 3 rounds per RAG spec §9, keyword+vector per C1 spec §5). Model-provider independence moves to v2.4.
v2.3 Task A: 9f02b64 + fix round 1dc4396 (I1–I5, M1–M9). The re-review found all addressed. A new minor is fixed: the system fold is always remembered.
v2.3 Task A: complete (review clean after 1 fix round).
v2.3 Task A: minor (deferred): zero-width characters inside a fence tag aren't normalised; a web page can fake the rounds-over note (it only ends the rounds early); the Ollama path has no system fold, because its templates map the role.
v2.3 Task B: 95f5a1d + fix round a2f71ad (I1 over-fetch cap; minors). The re-review found all addressed. After it: search_chunks is exact per document (an HNSW post-filter loss, reproduced red), and the already-shown markers are collapsed. Task B is complete.
v2.3 Task B: minor (deferred): the relevance buckets and floor are calibrated on nomic without prefixes; Task G re-measures after Task E. C2 library-wide scope will need an approximate search (iterative_scan on pgvector 0.8+) rather than the exact per-document scan.
v2.3 Task C: 56b23af (review: Needs fixes, one Important: the continuation hint came from the request, not from what was returned). Fix round in the next commit.
v2.3 contract for Task E: every split part after a page's first has chunk_type + extract.CONTINUATION_SUFFIX ("+cont"), with an exact-character overlap; test join_chunks(split(page)) == page.
v2.3 contract for Task D: the web_search guard records the document text it returned itself (prefetch passages, search passages, pages read including cut ones), not ctx.shown, which holds whole chunks only.
v2.3 Task C: fix round a1b75fa. The re-review found all addressed; its 2 new minors are fixed in the next commit (a deterministic top-k tiebreak inside the CTE; the cut-page wording). Task C is complete.
v2.3 Task D: a03cfaa (review: Needs fixes — the guard missed scripts without spaces and quotes in earlier answers; README). Fix round in the next commit.
v2.3 Task D: fix round 56970bc. The re-review found all addressed. New minors: the refusal wording now covers earlier answers (fixed next). Deferred: 12 characters is strict for Thai (errs toward refusing); unspaced fragments are joined across the whole text; history isn't capped and _runs is rebuilt on every call (CPU only); CJK Extension B+ and fullwidth forms fall back to word runs. Task D is complete.
v2.3 Task E: a2221d1 (review: Needs fixes — identical split parts collapsed on UNIQUE(doc_id, text_hash); no backoff after a failed rebuild). Fix round in the next commit.
v2.3 local check (deferred): on a real upgraded database, count indexed documents with a NULL embedding_model; ensure_current treats them as the same model.
v2.3 Task F: 12b1df3 (review: Needs fixes — the description and guidance still steered away from labels; unused GIN; unbounded floor bypass). Fix round in the next commit.
v2.3 Task E: fix round 1 eaa42c4. The re-review found M4 open (the clamp fell back to a default that could exceed the embedding input). Round 2 is in the next commit: clamp, one shared parse, gate-first lock order restored, a backoff when no text is found. Deferred: no test for the dropped-swap path; _failed isn't pruned; no end-to-end test of a scheduled rebuild through a real query.
v2.3 Task F: fix round deba05a. The re-review found all addressed; its 2 stale comments are fixed in the next commit. Deferred: a strong top word match uses up a bypass slot, and the slots are per document (relevant for C2). Task F is complete.
v2.3 Task E: fix round 2 27a6788. The re-review found all addressed; its 3 minors are fixed in the next commit. Task E is complete.
v2.3 Task G: 29e4cb8 (review: Needs fixes — CRITICAL: the eval provisioned through the OIDC resolver and took over an unclaimed seed admin; web search not started; sessions left behind on error; the refusal regex was weak). Fix round in the next commit.
v2.3 Task G: fix round 551cc31. The re-review found all addressed; its minors are fixed in the next commit. Deferred: a figure in words passes the absent check; no pre-turn budget refusal. Task G is complete.
v2.3 final review (whole branch): Needs fixes — Important 1: scripts/eval_doc_qa.py imported server modules before load_dotenv(), so the chunk and embedding settings ignored .env. Fixed 37bb7c5 (import inside main(); a subprocess test, red without the fix). Important 2: the thresholds were never measured under the prefixes.
v2.3 final review, Important 2 — measured 2026-10-05 in the cloud sandbox (Ollama + nomic-embed-text, real chunker; script and output in this folder, score-calibration-2026-10-05.*). Questions a document doesn't answer score as high as ones it does (book: 0.67-0.73 against 0.66-0.78); z-scores and top-1/top-10 gaps overlap too. Ruling: drop the strong/moderate/weak label (5eb36ff), because it told the model a passage answers when it may not. Keep the 0.45 floor as a noise floor. The rebuild-window query prefix isn't needed (a prefixed query against unprefixed chunks ranks no worse). The prefetch gate (0.6) passes small talk on the long book ("thanks!" 0.62): left for the prefetch redesign the user raised. Cost if wrong: a model that leaned on the label for "nothing relevant" now has to read; the eval below didn't change with it.
v2.3 eval, first real runs (cloud sandbox, llama3.2:3b on CPU, no SearXNG, so web_search fails with ConnectError): prefetch on 2/5 ×4 runs; prefetch off (CHAT_PREFETCH_MIN_SCORE=1) 1/5, 1/5, 2/5. Right content (fact, or a refusal): on 12/15, off 7/15. With prefetch off the model calls web_search for "What is the project's codename?" every time and never searches the document; with it on, most failures are the missing "(page N)" (once a wrong page). Every run used one tool round only. Takeaway: a 3B model's tool choice is the weak link and prefetch carries it; citations are the next weak link.
v2.3 final review minors (deferred to the next plan): run_embed records the profile even when some chunks failed to embed; the first tools-rejected turn still lists tools in the rules; the not-indexed line says "click Index" while it is extracting or indexing; lowering CHUNK_OVERLAP_CHARS repeats text during the rebuild window (comment wanted); the eval's two-hop and page-read cases can pass without the tool journey (report "PASS (no trail)"); README's /v1/docs/{id}/search line (HNSW wording, the 409) and a CHANGELOG line for the 409.
v2.3 eval, gemma4:e4b (cloud sandbox, CPU, no SearXNG): prefetch on 3/5, 3/5 — zero tool rounds whenever passages were prefetched, so two-hop and exact-label failed on incomplete passages; prefetch off 4/5 (one run) — searched the document every time, never the web; only a missing page citation. Opposite of llama3.2:3b: prefetch helps the 3B model and hurts the capable one. Next: docs/superpowers/handoff/2026-10-05-v2.4-document-context/HANDOFF.md.
