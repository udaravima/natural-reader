### Task 10: Provider errors say what happened; OpenRouter is verified

**Why:** In a check against a real OpenRouter key (2026-09-29), OpenRouter's free models answered `429` with the body `{"error": {"message": "Provider returned error", "metadata": {"raw": "… is temporarily rate-limited upstream. Please retry shortly …"}}}`. The user saw "The model provider returned an error: Provider returned error", which doesn't say that retrying later, or picking another model, would help. The same check verified with paid Gemma 4 and Mistral Small 3.2:
- the OpenAI-compatible adapter against OpenRouter across three turns;
- pins;
- a native `web_search` tool round;
- an image with a follow-up;
- provider-reported usage.

**Change:**
- In the shared provider error mapping (`server/llm/providers/base.py` and the adapters), map the HTTP status to a plain message. The existing `safe_error_message` redaction still runs on any provider text included.
  - `429` → "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model." Append the provider's `error.metadata.raw` (redacted, trimmed) when present.
  - `401` / `403` → "The provider rejected this server's credentials. An admin needs to check the API key."
  - `402` → "The provider account is out of credit."
- Keep the error code `provider_error`, and keep the "no output and no usage → not charged" rule.
- **Docs:** `README.md`, `docs/DEPLOYMENT.md` and `docs/USER_GUIDE.md`, where they describe providers, say OpenRouter was verified (the models above), and that vLLM and LiteLLM use the same protocol but weren't run.
- **Env naming:** the env example must make clear that the key goes in `INFERENCE_<NAME>_API_KEY`. A bare `OPENROUTER_API_KEY` is ignored; the user tripped on exactly this.

**Tests:**
- An adapter test per status (429 with `metadata.raw`, 401, 402) through `httpx.MockTransport`, asserting the message.
- A test that an `sk-…` inside `metadata.raw` is redacted.

