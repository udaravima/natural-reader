# Final fixes, round 2: report

1. Replies held back: `_held_replies` in server/chat/context.py (walk in order; exposure = a user message with a real_values text file exposes its model; assistant reply from another exposed model, or with no model, after that is replaced by the marker; the trimmer counts the marker). Tests in server/tests/test_chat_context.py (the exact walk, A-B-A, reply before the file, unexposed model, masked file, null/empty model, marker cost, replies-only note). RED: 9 failed, 50 passed. GREEN: 171 passed (context + web_consent). seen_text untouched.
2. Notices became notes (file_held_back {names, replies}, file_larger_than_context {name}); ContextNotes draws them; BuiltContext.notices, the orchestrator loop, msg.notices and its test removed. data-notice route kept. Tests: ChatView.status.test.jsx (RED 5 failed, GREEN 10 passed), test_chat_web_consent.py.
3. detailOf reads detail.message first, then a string detail. useVoiceInput.test.js uses the real shape with one case per server/routers/speech.py refusal (RED 10 failed, GREEN 86 passed with ChatView.voice). No other new caller reads a refusal detail (grep).
4. Docs: USER_GUIDE, CHANGELOG, DEPLOYMENT, .env.example, TESTING updated as ruled.
5. Dead code: BuiltContext.real_values, current_real_values, AudioTooLong import removed.

Full runs: pytest 2646 passed, 16 skipped, 2 deselected; vitest 1480 passed (126 files); eslint clean.
