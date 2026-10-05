### Task G: An evaluation harness a person can run against their own model

**Change:**
- `scripts/eval_doc_qa.py` builds a small multi-page fixture document with planted facts (single-hop, two-hop, exact-label, absent). It indexes the document and runs the real chat turn path against a configured model. For each question it reports:
  - tools called and rounds used;
  - whether the answer contains the expected fact;
  - whether it cites the right "(page N)";
  - whether it refused correctly on the absent fact.

  It needs Postgres, the embedding model and a chat model, so it's for the local machine.
- **Docs:**
  - `docs/CHAT_WITH_PDF.md` (how retrieval works now);
  - `.env.example` (the new settings);
  - README.

**Tests:** the harness's scoring functions (pure) are unit-tested; the live run is manual.

