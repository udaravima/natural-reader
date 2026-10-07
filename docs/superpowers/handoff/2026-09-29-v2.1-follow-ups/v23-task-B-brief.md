### Task B: `search_documents` says what it is, and filters noise

This is renamed from `search_document` to the C2 name. Saved chats that used the old name keep their citations and tool calls: the renderer accepts both names.

**Change:**
- **The description** (short enough for a 3B model):
  - searches the open document only, by meaning (vector search) and by exact words (Task F);
  - returns passages with their reader page;
  - `k` is 5 by default, up to 10 for broad questions;
  - the question's own words were already searched before the model ran, so search again with different words, names or terms;
  - when a passage points elsewhere (a name, a table, a section), search for that, or read the page (Task C).
- **Results:**
  - Each passage has a page and a `relevance` of strong / moderate / weak (bucketed from the score). The raw score stays in the saved summary and logs only.
  - Passages below `CHAT_SEARCH_MIN_SCORE` (default 0.45) are dropped.
  - With nothing left, the result says `"No passages about this in the document."`.
  - Passages already shown earlier in this turn come back as `{"page": N, "already_shown": true}` without their text, so repeated searches surface new material and context doesn't balloon.
- The saved summary keeps `docId` and `docName` (Task 6 citations).

**Tests:**
- the floor;
- relevance buckets;
- the "already shown" marking across two calls in one turn;
- the empty-result wording;
- the description names no other tool that isn't offered.

