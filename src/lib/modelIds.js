// Model ids are `provider:name` (C1 spec §4.3). Pure helpers for the model
// picker, and for carrying a pre-C1 saved selection ("qwen2.5:7b") to its id.

export function migrateModelId(saved, models) {
    if (!saved || !Array.isArray(models) || models.length === 0) return saved;
    if (models.some((m) => m.id === saved)) return saved;
    const match = models.find((m) => m.kind === 'ollama' && m.name === saved)
        || models.find((m) => m.name === saved);
    return match ? match.id : saved;
}

export function groupByProvider(models) {
    const groups = new Map();
    for (const m of models || []) {
        if (!groups.has(m.provider)) groups.set(m.provider, []);
        groups.get(m.provider).push(m);
    }
    return [...groups.entries()];
}

export function capabilityBadges(model) {
    const c = model?.capabilities || {};
    return [c.tools && 'tools', c.thinking && 'thinking', c.vision && 'vision', c.audio && 'audio'].filter(Boolean);
}

export function modelLabel(model) {
    const badges = capabilityBadges(model);
    return badges.length ? `${model.name} · ${badges.join(' · ')}` : model.name;
}

// Which per-model settings a provider honours (spec §4.2): context size and
// keep-alive are Ollama-only; thinking hides only when the model says it can't.
// Unknown model (list not loaded yet) = show everything, today's behaviour.
export function supportedKnobs(model) {
    const ollama = !model || model.kind === 'ollama';
    return { numCtx: ollama, keepAlive: ollama, think: model?.capabilities?.thinking !== false, numPredict: true };
}
