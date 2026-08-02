// Pure helpers for assembling the Ollama /api/chat `messages` array.
// Extracted from useChatEngine so the ordering can be unit-tested.

// Map an internal chat message to the shape Ollama's /api/chat expects.
//
// Ollama's native /api/chat requires `content` to be a plain string (structured
// content blocks return HTTP 400). The `Message` struct has ONE binary-input
// field: `images: [base64]`. There is no `audios` field, so audio attachments
// are also fed through `images` — for audio-capable models the runtime treats
// those bytes as audio. Attachments stripped on session save (no base64) are
// skipped; the historical message just goes back as text.
export const buildApiMessage = (m) => {
    const atts = Array.isArray(m.attachments) ? m.attachments : [];
    const binaryB64 = atts
        .filter(a => a && a.base64 && (a.kind === 'image' || a.kind === 'audio'))
        .map(a => a.base64);
    const out = {
        role: m.role,
        content: m.content || '',
    };
    if (binaryB64.length) out.images = binaryB64;
    return out;
};

// Assemble the full message history for a /api/chat request.
//
// The context preamble (the attached excerpt + any semantically-retrieved
// passages) must sit IMMEDIATELY BEFORE the current user message — not at the
// front of the whole history. Otherwise, on a multi-turn chat the excerpt is
// stranded behind all prior turns and the model treats it as stale leading
// boilerplate ("no content attached"). On a fresh chat priorMessages is empty,
// so the excerpt is adjacent to the user message either way.
export const buildChatHistory = ({ priorMessages = [], contextPreamble = [], userMsg }) =>
    [...priorMessages, ...contextPreamble, userMsg].map(buildApiMessage);
