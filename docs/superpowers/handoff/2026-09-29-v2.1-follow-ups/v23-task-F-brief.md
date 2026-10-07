### Task F: Exact-word matching alongside meaning (hybrid search)

**Change:**
- Migration 014 (with Task E) adds `doc_chunks.text_search tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED` with a GIN index. The `'simple'` configuration works in any language and keeps numbers and labels like "4.2".
- `search_chunks` runs both searches and fuses them by reciprocal-rank fusion.
- A passage found by words but weak by meaning still passes the floor, marked `match: "words"`.

**Tests:**
- "Table 4.2" is found by words when meaning search ranks it low;
- fusion order;
- the migration on an existing database.


**Deviation (deliberate):** the tsvector column ships in its own migration 015 (Task E's 014 carries only the profile).
