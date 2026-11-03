# Choosing the speech model

**Date:** 2026-11-02. **Status:** released in v2.5.0. Since v2.7.0 the model list applies only while Whisper on the server hears speech: with an OpenAI-compatible server or Google Cloud, the engine brings its own model ([Google speech](2026-11-03-google-speech-design.md)).

## Why

Voice input runs one Whisper model, named by `SPEECH_MODEL`, and changing it means editing the environment and restarting. Stock Whisper `small` is weak at Sinhala and Tamil. Fine-tuned models exist, but they ship in Transformers format and faster-whisper can't load them. The owner wants those models selectable, from the admin panel as well as from configuration, as a stopgap until audio can go straight to a model.

## Goals

- **Admin → Chat options → Voice input → Speech model** lists the models and switches between them with no restart.
- An admin can add a model from Hugging Face in the same place, and remove one they added.
- A deployer can do the same from a file (`SPEECH_MODELS_FILE`) or from `SPEECH_MODEL`.
- A Transformers-format Whisper model works without anyone running a converter by hand.

## Non-goals

- A model per language, or a model per person. One model runs at a time for the whole deployment, so a server can't specialise in Sinhala and Tamil together. It would need several models in memory; that's a later decision.
- Choosing a model for a remote engine (`SPEECH_ENGINE=openai`): `SPEECH_MODEL` still names it, and the console hides the list.
- Sending audio straight to a model. The `Transcriber` interface stays the seam for that.

## Anti-goals

- This must not make the base image bigger. `torch` and `transformers` stay in the Docling build.
- A bad catalogue entry, a typo or a Hugging Face outage must never stop the server or take down the other models.

## Decisions

| Decision | Why | Cost if wrong |
|---|---|---|
| One model for the deployment, chosen by an admin | Each model costs about 1 GB of memory on a CPU-only box | A server with Sinhala and Tamil speakers serves one language well |
| The list is three layers, later ids replacing earlier: built in, `SPEECH_MODELS_FILE`, admin-added | Config for deployers, the panel for admins, and air-gapped servers can't reach the Hub to add one | Three places to look |
| Admin-added models are stored in the `chat_options` row | The row is already read per request on every worker, so no migration | The row grows by a few hundred bytes per model |
| Converted on first use, in a separate process, into `HF_HOME/speech-converted/` | `torch` otherwise never loads in the backend, and its memory goes back when the conversion ends | One short CPU spike per model per server |
| Adding a model looks it up on the Hub, refuses what isn't Whisper, and pins the commit | A typo fails at once with a reason, not later on a load | Air-gapped servers use the file |
| A worker swaps at once and frees the old model, instead of keeping it until the new one is ready | Two models in memory is the failure on a small box | Voice says "still getting ready" during a switch |
| With **Auto**, a single-language model is told its language | Measured: run12 called Sinhala Kannada at 0.92, and the Tamil model's config lists no languages, so detection crashes | Someone speaking another language gets that language's script. Settings says what the model is for |
| An explicit Speech language is honoured | run15 still reads English at about 8% WER by its model card, so forcing Sinhala would be worse | A person can pick a language the model handles badly |
| Every local path runs Silero VAD at threshold 0.4, the unconverted one included | Measured: without VAD, silence became 82 characters of Tamil and a 3 s pause lost a whole Sinhala sentence. The default 0.5 cut real speech and doubled Tamil error (median CER 0.240 against 0.136). Below 0.35, loud noise came out as invented words | A quiet word can still be cut |
| `temperature=0`, with Whisper's default beam and conditioning | Measured on the final settings: stock `small` on 8 Sinhala clips 171 s to 27 s, the worst Tamil clip 18.2 s to 2.7 s, a 269-word English recording word for word the same. `beam_size=1` changed real Sinhala words for about 13% of the time, and `condition_on_previous_text=False` dropped the last two words of that English recording. English is the primary language for now | A clip that fails, fails once, without the retries that sometimes rescue it. Recordings over 30 s run about a quarter slower than with conditioning off |
| `converted-whisper` and `transformers-whisper` are refused up front without `torch` and `transformers`, with the reason in the list | The base image doesn't have them, by the anti-goal | The base image offers only CTranslate2 models, unless a model was converted elsewhere |

## The pasted list

The three models are built in, with one correction: the pasted revision for `whisper-small-si-run15` (`96eac62…`) belongs to the run12 repo. That repo's real `main` is `ab6154e9f312ce3f8803e624a84942213f523af3`. The owner's benchmark never tested run15, so no score is claimed for it. Two more from the same research are built in: `whisper-small-si` (run12 converted, the benchmark's Sinhala winner) and `whisper-turbo`.

## Not verified

Sri Lankan Tamil specifically, and a fine-tune for a language Whisper doesn't list: it can be added, with no language set, but nobody has tried one. See `docs/DEPLOYMENT.md` for what was measured.
