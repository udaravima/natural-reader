// Pure helpers for Ollama inference settings, stored per model.
// Nothing here touches React or localStorage — App.jsx owns the state.
//
// Governing rule: an unset setting is OMITTED from the provider request (the
// SPA sends it as null; the server drops it — see toWireSettings). Sending
// `null` or `0` instead would replace a model's tuned Modelfile values (for
// example qwen3.5 ships temperature 1 / top_k 20 / top_p 0.95) with no error.

export const INFERENCE_DEFAULTS = {
    numCtx: null,      // null = let Ollama size the context window from free memory
    keepAlive: null,   // null = Ollama default (5m); -1 = never unload
    think: 'off',      // 'off' | 'on' | 'low' | 'medium' | 'high'
    numPredict: null,  // null = -1 (infinite generation)
};

export const resolveForModel = (map, model) => ({
    ...INFERENCE_DEFAULTS,
    ...((map && map[model]) || {}),
});

export const patchForModel = (map, model, patch) => ({
    ...map,
    [model]: { ...resolveForModel(map, model), ...patch },
});

// The turn body's `settings` (C1 spec §7.1). An unset value is null and the
// server leaves it out of the provider request, so a model's own tuned
// defaults (its Modelfile values) stay in force. `knobs` is supportedKnobs()
// for the chosen model: a knob its provider doesn't honour goes as null, so a
// num_ctx left over from an Ollama model can't set the server's trimming
// window for an OpenAI-kind one. Omitted = send everything.
export const toWireSettings = (settings, knobs = {}) => {
    const s = { ...INFERENCE_DEFAULTS, ...(settings || {}) };
    return {
        think: s.think,
        num_ctx: knobs.numCtx === false ? null : s.numCtx,
        keep_alive: knobs.keepAlive === false ? null : s.keepAlive,
        num_predict: s.numPredict,
    };
};

export const migrateLegacyThinking = (bool) => (bool ? 'on' : 'off');

// Rough token estimate for the context meter. chars/4 is deliberate — a real
// tokenizer would mean shipping per-model vocabularies to the browser for a
// readout whose only job is to say "you are getting close".
export const estimateTokens = (texts = []) =>
    Math.ceil(texts.reduce((n, t) => n + (typeof t === 'string' ? t.length : 0), 0) / 4);

// Ollama reports done_reason "length" for TWO different events: the context
// window filled up, OR num_predict (Max reply tokens) was reached. Those need
// different advice — telling a user to raise the context window when their
// reply cap fired sends them to the wrong control. Distinguish by comparing
// the reply length against the numPredict cap that was actually in effect.
// Returns null for every other done_reason so the caller can toast
// unconditionally on a non-null result.
export const truncationMessage = (stats, settings = INFERENCE_DEFAULTS) => {
    if (!stats || stats.doneReason !== 'length') return null;
    const prompt = stats.promptEvalCount || 0;
    const reply = stats.evalCount || 0;
    if (settings?.numPredict && reply >= settings.numPredict) {
        return `Reply hit the ${settings.numPredict}-token reply cap. `
            + 'Raise "Max reply tokens" in Inference settings.';
    }
    return `Reply was cut off — context full (${prompt} prompt + ${reply} reply = ${prompt + reply}). `
        + 'Raise the context window in Inference settings.';
};
