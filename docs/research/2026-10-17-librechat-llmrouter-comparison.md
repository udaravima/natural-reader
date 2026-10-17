# LibreChat and LLMRouter, feature by feature, against Natural Reader

Read on 2026-10-17 from local clones: LibreChat `e081b566a`, LLMRouter `c65ee01`
(both MIT). Sources: each project's README, LibreChat's `librechat.example.yaml`,
`api/server/routes/`, `client/src/components/Nav/`, and LLMRouter's
`openclaw_router/`. "Us" means v2.3.0.

## Can their code be imported?

**LibreChat: ideas, rarely code.** It is Node.js + MongoDB + TypeScript React;
we are FastAPI + Postgres + JavaScript React. MIT allows copying, but almost
nothing transplants: its services sit on Mongoose models and its UI on its own
component library and Jotai/Recoil state. What carries over is design: data
shapes (a memory entry, a preset), UX (the settings dialog), and protocols
(resumable streams, MCP). Two pieces are separable:

- **MCP** is a protocol, with an official Python SDK, so we can implement the
  same feature natively.
- **LibreChat's RAG** is a separate Python service (`rag_api`, FastAPI +
  pgvector). We already have our own, with page citations, versions and
  projects that it lacks.

**LLMRouter: the routing library can be imported as-is.** It is Python on PyPI
(`llmrouter-lib`), and its `openclaw_router` is an OpenAI-compatible FastAPI
server, so our gateway could even call it as one more provider. Its trained
routers need labelled data (query → which model did best) that we don't
collect yet.

**Legend.** *Port* = rebuild the design in our stack. *Reuse* = depend on
their library or protocol. *Have* = we already do this. *Skip* = not worth it
for a reading app. Effort: S (days), M (a week or two), L (a spec-sized project).

## 1. Chat experience

| Feature | LibreChat | Us today | Later? | Effort |
|---|---|---|---|---|
| Streaming replies, Stop | yes | yes | Have | — |
| Reply survives leaving the view | yes | **no: leaving Chat aborts it** (deliberate effect in `App.jsx`) | Phase B fix | S |
| Reply survives closing the tab / another device (resumable streams: server job, re-attach) | yes (`GenerationJobManager`) | no: a disconnect saves the turn `aborted` | Port | M–L |
| Edit a message and resubmit, regenerate | yes | no | Port | M |
| Fork / branch a conversation | yes | no | Port (needs message tree) | M |
| Search all chats | yes (Meilisearch) | no | Port with Postgres full-text (we already use it for chunks) | S–M |
| Bookmarks / tags on chats | yes | no | Port | S |
| Share a chat by link | yes | no | Port (we have share plumbing for documents) | M |
| Import (ChatGPT, LibreChat) / export (md, json, screenshot) | yes | partial (message audio export) | Port export first | S |
| Temporary chat (not saved) | yes | browser-only chats exist | Have, roughly | — |
| Summarize/compact long chats | yes | no (we trim to the context window) | Port | M |
| Side-by-side two models (`multiConvo`) | yes | no | Skip for now | M |
| Copy message | yes | yes | Have | — |
| Reasoning display | yes | yes | Have | — |
| Feedback (thumbs) | yes | no | Port, feeds a future router | S |

## 2. Roles, prompts and memory

| Feature | LibreChat | Us today | Later? | Effort |
|---|---|---|---|---|
| Presets / model specs with their own instructions, switchable mid-chat | yes | **one document-centred prompt** ("librarian") | Port: assistant roles, Phase C | M |
| Hard vs soft admin defaults (`default` / `softDefault`) | yes | soft only: a browser-stored default never reaches existing users | Port: admin defaults, Phase A | S |
| Prompt library with variables, shared with groups | yes | no | Port after roles | M |
| Per-user memory (key/value, token-capped, Personalization tab, optional auto-update agent) | yes | per-chat pins only | Port, Phase C or after | M |
| Admin assistant profile (identity, tone) | via specs | yes (Admin → Assistant) | Have | — |
| Agents (no-code assistants, shareable, marketplace) | yes | no | Port a small version as "roles" first | L |
| Skills (`SKILL.md` bundles), subagents | yes | no | Skip for now | L |

## 3. Documents and retrieval

| Feature | LibreChat | Us today | Later? | Effort |
|---|---|---|---|---|
| Upload a file from the chat box | yes | **no**: only via the reader or Library | Port, Phase C | M |
| Chat with one document | yes | yes, with page citations that open the page | Have, ahead | — |
| **Search across many documents** | yes (file search over an agent's files) | **pending: C2.1 project chat** | Our own design (C2.1) | L |
| File citations | yes | yes, by page | Have, ahead | — |
| Document versions, projects with roles | no | yes | Ahead | — |
| PDF → Markdown conversion (Docling) | OCR options | yes | Have | — |
| Web search | yes (providers + scrapers + rerankers) | yes (SearXNG, capped fetches) | Have; reranking is a port | S |

## 4. Tools and integrations

| Feature | LibreChat | Us today | Later? | Effort |
|---|---|---|---|---|
| MCP servers as tools | yes | no | Reuse (MCP Python SDK) | M |
| OpenAPI "actions" | yes | no | Skip; MCP covers it | — |
| Code interpreter (sandbox) | yes | no | Skip for a reading app | L |
| Artifacts (React/HTML/Mermaid previews) | yes | no | Port Mermaid only, maybe | S–M |
| Image generation | yes | no | Skip | — |
| Scheduled agent runs | yes | no | Skip | — |

## 5. Speech

| Feature | LibreChat | Us today | Later? | Effort |
|---|---|---|---|---|
| Text-to-speech | cloud (OpenAI, Azure, ElevenLabs) | **local Kokoro, 27 voices, sentence highlighting, audiobook export, Chrome extension** | Ahead | — |
| Speech-to-text | yes | voice recording as audio attachment | Port STT (e.g. Whisper) | M |
| Hands-free conversation mode | yes | auto-read replies (to be off by default) | Port | M |

## 6. Accounts, admin and operations

| Feature | LibreChat | Us today | Later? | Effort |
|---|---|---|---|---|
| Settings as a modal with tabs | yes | separate page that stops narration | Port, Phase B | S–M |
| Profile: avatar | yes | no | Port, Phase B | S |
| Change password, 2FA, passkeys | built in | in Keycloak (its account console) | Link to Keycloak, Phase B | S |
| Session expiry (15 min access + 7-day refresh) | yes | fixed 7 days, **no idle timeout** | Phase A | S |
| OIDC / LDAP / email / social logins | yes | OIDC via Keycloak (which federates LDAP and social) | Have, via Keycloak | — |
| Admin panel: users, groups, per-role permissions, live config | yes | users, capabilities, projects, assistant profile | Port groups + live config later | M |
| Token credits / balance | yes | daily token budget per user | Have, roughly | — |
| User-provided API keys | yes | no (deployment keys only) | Port if wanted | M |
| Moderation / PII filters | yes | prompt-injection fencing only | Port later | M |
| Observability (OpenTelemetry, Langfuse) | yes | logs + audit log | Port OTel later | M |
| 40+ UI languages | yes | English | Skip for now | L |
| Helm, S3/CDN storage | yes | compose + podman, local storage | Skip for now | — |

## 7. Model routing (LLMRouter)

| Feature | LLMRouter | Us today | Later? | Effort |
|---|---|---|---|---|
| One endpoint, many providers | yes (`openclaw_router`) | yes (model gateway: Ollama, OpenAI-compatible, OpenRouter) | Have | — |
| `model="auto"`: pick a model per query | rules, random, round-robin, LLM judge, 16+ trained routers | no | Port rules / LLM-judge first; Reuse `llmrouter-lib` once we log outcomes | M |
| Routing memory (similar past queries → model) | yes (Contriever) | no | After feedback exists | M |
| Key rotation per provider | yes | one key per provider | Port if rate limits bite | S |

## Where we are ahead

Reading and listening are ours alone: page-accurate narration with
highlighting, audiobook export, the read-aloud extension, answers that cite
and open the page, document versions, and projects with roles. Every
borrowed feature should serve that, not turn this into a second LibreChat.

## Suggested pick-up order

1. **Phase A (now)**: admin defaults (hard/soft), session idle timeout, and
   the bugs (write-before-commit, audiobook cancel, thinking off).
2. **Phase B**: settings modal, narration across views, chat side panel,
   profile with avatar and a Keycloak account link.
3. **Phase C + C2.1 together**: assistant roles, upload in chat,
   **multi-document RAG**; then memory.
4. Later: chat search, edit/regenerate, export, MCP, STT, auto-routing.
