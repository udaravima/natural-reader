# v2.11.0 — Choose the embedding model, and change it without losing search

**An admin can now pick the embedding model in the console, and switching models no
longer means a library that can't be searched.** Search compares a question's vector
with every passage's, so all of them must come from one model. Until now the model was
fixed by `EMBEDDING_MODEL` in `.env`, and nothing re-embedded the passages already
indexed when it changed. This release re-embeds the whole library in the background
with the new model while search keeps using the old one, then switches both in one step.

**And the admin console no longer loads the embedding model just by being opened.** The
Overview used to check the model by embedding a sentence, which kept it in memory for
five minutes and looked like the chat was holding it.

Out of the box nothing changes: the model in `.env` (`nomic-embed-text` by default)
stays the one in use, and nothing is re-embedded.

## Added

### 🔎 Admin → Models → Embeddings

- Pick a **provider** and a **model** (the provider's own list, embedding models first),
  press **Check**, then **Use this model**. **Back to the server's setting** removes the
  console's choice.
- **Check measures what the model gives.** It admits a model that gives 768 numbers, or
  one trained to be shortened to 768 (`qwen3-embedding`, `embeddinggemma`,
  `nomic-embed-text`, `text-embedding-3-*`). It refuses any other, with its size and the
  reason: Ollama cuts any model's vector to the size asked for without an error, and a
  1024-number model cut to 768 searches noticeably worse.
- **Embeddings through any provider.** `EMBEDDING_PROVIDER` names one of
  `INFERENCE_PROVIDERS`, whose URL and key are reused; an OpenAI-compatible one is called
  at `<its URL>/embeddings`. Passages go 32 per request instead of one call each.
- `embeddinggemma`'s trained prefixes are known, as `nomic-embed-text`'s were.

### 🔁 Changing the model re-embeds the library, then switches

- **Every document stays searchable while it runs.** The new vectors fill a second
  column; questions are still embedded with the current model until every passage has a
  new vector, then one transaction swaps the columns and makes the new model current.
- **You can see how far it is.** A bar and "1,234 of 5,000 passages (24%), about 7
  minutes left", in Models → Embeddings and on the Overview, counted from the table, so
  passages indexed meanwhile count too. With the box open, a message says when the
  re-embed is done, paused, or stopped from another tab.
- **Cancel the re-embed** keeps the current model. A restart or a crash resumes where it
  was; with several workers, one fills at a time.
- The same model on another provider switches at once, since the vectors don't change.
- A changed `EMBEDDING_MODEL` in `.env` starts the same re-embed at the next start.

## Changed

- **Rebuilds of one document are queued in the database** (the new `doc_rebuilds`
  table) and claimed by one worker at a time: with several workers a document is rebuilt
  once, a restart resumes the queue, and the Overview counts waiting and failed rebuilds.
- **`EMBEDDING_TIMEOUT_S` defaults to 120 s** (was 30), since one request now carries 32
  passages.
- **A People tick changes only that capability.** `PATCH /v1/admin/users/{id}` takes
  `add_capabilities` and `remove_capabilities`, applied to the person as they are now.
  `capabilities` (the whole set) still works; a request can't send both.

## Fixed

### Embeddings

- **Opening the admin console loaded the embedding model.** The Overview now reads
  whether the model is pulled and whether it's in memory, and never loads it. Its size
  is checked once, when it's chosen or once in the background after a start, and kept.
  **Check** unloads a model it had to load; **Use** keeps it loaded only when a
  re-embed follows, since the re-embed would load it again seconds later.
- **A fresh install with a shortenable `EMBEDDING_MODEL` couldn't index.** A model such
  as `qwen3-embedding` was made current without the size to ask for, so every request
  came back with 1024 numbers and failed.
- **Embedding models were offered as chat models**, apart from the one in `.env`: a
  model pulled to try as an embedder failed every chat message with HTTP 501.
- **A passage the new model refused** as too long (a small model on vLLM or OpenAI)
  paused the re-embed for good. It's now sent on its own and halved until it fits, down
  to 256 characters.
- Changing the model again mid-re-embed finished the first model before starting the
  second; it now stops or starts over. Workers judged a stalled re-embed by their own
  clocks; they use the database's. A misconfigured provider logged its warning on every
  query; it logs once.

### From the bucket list

- **`BOOTSTRAP_ADMIN_EMAIL` now reserves the admin account.** With it set, whoever
  signed in first on a fresh database became the admin anyway. Now only that email,
  verified, claims it; anyone else signing in first waits as a pending member. That
  founder gets admin, reader and chat on their first login.
- **A People tick no longer takes away roles a login brought.** It sent the whole set
  the list showed, so a list opened before someone's login undid that login's roles, in
  Keycloak too.
- **A full database pool no longer stalls every signed-in request.** Stamping "last
  seen" now waits at most half a second and is otherwise skipped until the next request.
- **A converted speech model no longer keeps its original**, about 925 MB for a `small`
  fine-tune. And a second worker asked for a model another is converting waits for that
  result instead of downloading it again.
- Stepping through the speech model or engine list with the arrow keys no longer loads
  each model on the way; the choice is made on Enter, on leaving the list, or a click.
- The Speaking chip says "Auto (Sinhala)" when Google's first ticked recognizer hears
  only one language.

## Upgrading

- **Migration 025** runs at startup and adds `doc_rebuilds`. Rebuild both halves: the
  backend image (`./startup.sh rebuild`) and the frontend (`npm run build`).
- **The first start records the `.env` model as current. Nothing is re-embedded**, with
  one exception: a deployment whose `EMBEDDING_MODEL` is an `embeddinggemma` now gets
  that family's trained prefixes, so its documents are rebuilt on first use.
- **If you set `EMBEDDING_TIMEOUT_S` yourself**, raise it to allow for 32 passages per
  request; unset, it's 120 s.
- **`BOOTSTRAP_ADMIN_EMAIL` behaves differently on a fresh database**: only that email
  claims the admin. Set it to the founder's address, or leave it unset for "whoever signs
  in first".
- **What a re-embed costs:** while it runs, about 6 KB more per passage (a second copy of
  the vectors and its index). On a laptop CPU with no GPU it took about 2.5 minutes per
  1,000 passages with `nomic-embed-text` and 4 to 6 with `embeddinggemma-2:740m`. Both
  models must stay reachable until the switch; if the new one stops answering, the
  re-embed pauses and retries every 30 seconds.
- **Disk back from earlier speech conversions:** versions up to 2.10 kept each converted
  model's original in the Hugging Face cache, about 925 MB each. How to remove them:
  [DEPLOYMENT.md, "Originals left by 2.10 and earlier"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md).
- **Coming from v2.9.0**, read v2.10.0's upgrading notes too (Google recognizers).
- **Downgrading:** cancel any re-embed first (the previous image doesn't know the second
  column), then run 025's Down block.
- All of it in detail:
  [DEPLOYMENT.md, "Embeddings: provider, model and re-embedding"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md#embeddings-provider-model-and-re-embedding).

## Known limits

- **The vectors column holds 768 numbers.** A model that gives another size and isn't
  trained to be shortened is refused. Changing the column's size means re-creating it
  and re-indexing by hand (`EMBEDDING_DIM` in `.env.example`).
- **Not called:** vLLM, OpenAI and OpenRouter's own embedding endpoints. Their requests
  follow the same OpenAI API, and Ollama's own OpenAI-compatible `/v1` was walked end to
  end as one.
- **A passage shortened to fit a model is noted only in the server log.**
- Re-embed speed on a GPU wasn't measured.

## Tests

3099 backend tests pass (16 skipped), 1849 frontend tests in 152 files, lint has no
errors, and the build works. Walked in Chromium at 1366 and 360 px against a real Ollama:
the Overview left a loaded model's expiry untouched and loaded nothing when none was in
memory; a 313-passage library re-embedded with two workers sharing one database (one
filled, one switch), with the bar and the time left counting down and the "done"
message; Cancel; a document question answered from the right page during and after;
the same over Ollama's `/v1` as an OpenAI-compatible provider, started on one backend
and finished by another.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#2110---2026-11-05) · diff:
[v2.10.0…v2.11.0](https://github.com/udaravima/natural-reader/compare/v2.10.0...v2.11.0)
