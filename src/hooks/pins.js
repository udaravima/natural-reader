// Pure reducers + guardrails for chat "pins" (persistent document excerpts).
export const MAX_PINS = 6;
export const PINNED_CONTEXT_CHAR_BUDGET = 12000;

let _seq = 0;
export const makePin = ({ doc_id = null, fileName = '', page = null, kind, text }) => ({
    id: `pin-${Date.now().toString(36)}-${(_seq++).toString(36)}`,
    doc_id, fileName, page, kind, text,
});

export const totalPinnedChars = (pins) =>
    pins.reduce((n, p) => n + (p.text ? p.text.length : 0), 0);

const samePin = (a, b) => a.doc_id === b.doc_id && a.kind === b.kind && a.text === b.text;

export const addPin = (pins, pin) => {
    if (pins.some((p) => samePin(p, pin))) return { pins, added: false, reason: 'duplicate' };
    if (pins.length >= MAX_PINS) return { pins, added: false, reason: 'max-pins' };
    if (totalPinnedChars(pins) + (pin.text?.length || 0) > PINNED_CONTEXT_CHAR_BUDGET) {
        return { pins, added: false, reason: 'budget' };
    }
    return { pins: [...pins, pin], added: true };
};

export const removePin = (pins, id) => pins.filter((p) => p.id !== id);

// Pin wording as the model sees it. The SERVER builds the real prompt
// (server/chat/context.py pin_messages, same text); this copy exists only so
// the composer's context meter can count what pins cost.
export const buildPinPreamble = (pins = []) =>
    pins.map((p) => ({
        role: 'system',
        content:
            `The user is reading "${p.fileName || 'a document'}".\n` +
            `Relevant excerpt (${p.kind || 'page'}` +
            `${p.page != null ? `, page ${p.page}` : ''}):\n\n` +
            `"""\n${p.text}\n"""\n\n` +
            `Use this excerpt as primary context for the user's question. If it does ` +
            `not contain the answer, say so or use the document search tool if available.`,
    }));
