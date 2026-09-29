# Task 10 report — provider errors say what happened; OpenRouter verified (cloud session, controller-implemented)

BASE 3fddf97.

## Change
- **`server/llm/providers/base.py`:** `provider_error(status, raw) -> ProviderError` is the shared mapping.
  - `429` → "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model." It adds " (Provider said: …)" when the body has `error.metadata.raw`. That detail has its whitespace collapsed, goes through `_redact_secrets`, and is trimmed to 200 chars with "…".
  - `401` / `403` → "The provider rejected this server's credentials. An admin needs to check the API key."
  - `402` → "The provider account is out of credit."
  - Anything else keeps `safe_error_message(raw)`, which is redacted as before.
  - Mapped errors carry `ProviderError.explained = True` (new keyword-only field in `server/llm/types.py`, default False).
- **Where the mapping is used:**
  - `open_stream`, the non-2xx path shared by both adapters. Its WARNING log still records the provider's own redacted text.
  - `openai_compat._entries` (`/models`).
  - The OpenAI stream's mid-stream `{"error": …}` payload. That uses the payload's `error.code` when it's an int ≥ 400, otherwise 500 as before.
- **`is_feature_rejection` excludes 402.** Before, an out-of-credit answer was retried up to twice as a possible feature rejection (without tools, then without reasoning). Now it makes one request.
- **`server/chat/orchestrator.py` `_provider_error`:** an explained error's message is shown as it is. Others keep the "The model provider returned an error: …" prefix. The code is still `provider_error`, and billing is unchanged.

## Tests
- `server/tests/test_llm_openai.py` (+9, through FakeUpstream / httpx.MockTransport). All but the "other statuses" pin were red before the change.
  - 429 with metadata.raw: plain text plus the detail, no "Provider returned error", explained.
  - 429 without metadata: plain text only.
  - 401 and 403.
  - 402, which also makes exactly one request (red before: 3 requests).
  - Other statuses keep the provider text and are not explained.
  - A key inside metadata.raw is redacted.
  - A long metadata.raw is trimmed.
  - A 429 mid-stream error payload is explained.
- `server/tests/test_chat_orchestrator.py` (+1): an explained ProviderError's message reaches the `error` event unchanged, with code `provider_error`.

## Live check with a temporary OpenRouter key (the user's, 2026-09-29)
The key was kept in a mode-600 file in the session scratchpad and never printed. The real backend ran through `POST /v1/chat/sessions/{id}/turns` with `INFERENCE_PROVIDERS=openrouter`, kind openai, and an allow-list of 4 models.
- **Free models** (`google/gemma-4-26b-a4b-it:free`, `qwen/qwen3.8-27b:free`): 5 turns, all 429. Each showed "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model. (Provider said: google/gemma-4-26b-a4b-it:free is temporarily rate-limited upstream. Please retry shortly, or add your own key …)".
- **Paid** `mistralai/mistral-small-3.2-24b-instruct`: two turns in one session. The reply was "OK", then "Teal" (it remembered). Provider-reported usage was 211/2 and 224/3 tokens, with `usageEstimated: False`.
- **Not charged:** `inference_usage` held exactly 2 requests, 435 prompt and 5 eval tokens. The 5 refused turns were not charged.
- **A wrong key against real OpenRouter:** the adapter gave 401 "The provider rejected this server's credentials…" (explained).
- **No key in the logs:** the backend log contains neither the key nor any `sk-or-v1` string.
- **Not run live:** 402 (it needs an account with no credit). Tests only.
- **Not re-run today:** pins, the web_search tool round and images were verified with this adapter on 2026-09-29 (handoff); this change doesn't touch them.

## Docs
- `DEPLOYMENT.md` § Configuring providers:
  - what has been run (OpenRouter plus the two paid models; vLLM and LiteLLM not run);
  - the `INFERENCE_<NAME>_API_KEY` trap: a bare `OPENROUTER_API_KEY` is ignored;
  - a table of what people see per status.
- `README.md` (model providers paragraph): OpenRouter is verified, vLLM and LiteLLM are not run, refusals say what happened.
- `USER_GUIDE.md` (chat section): what each message means and who should act.
- `.env.example`: the key-naming note under `INFERENCE_OPENROUTER_API_KEY`.
- `CHANGELOG`: one Fixed line.

## Suites
- backend: 607 passed;
- frontend: 390 passed;
- eslint: clean.
