// Pure text helpers for the read-aloud extension. No chrome.* or live globals —
// callers pass in the Document so this is unit-testable under jsdom.

export function splitSentences(text) {
    const clean = (text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return [];
    // Split at whitespace that follows a sentence terminator. Keeps every
    // character, so a terminator not followed by a space (e.g. "$3.99",
    // "1.800.555.1234") stays inside its sentence. Simple by design —
    // abbreviations like "Dr." may still over-split (accepted v1 limitation).
    return clean.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

export function chunkSentences(sentences, size = 30) {
    const step = size > 0 ? size : sentences.length || 1;
    const out = [];
    for (let i = 0; i < sentences.length; i += step) {
        out.push(sentences.slice(i, i + step));
    }
    return out;
}

function readableText(el) {
    if (!el) return '';
    // innerText respects rendering in a real browser; jsdom leaves it undefined,
    // so fall back to textContent for tests.
    return (el.innerText != null ? el.innerText : el.textContent) || '';
}

export function extractSelectionOrPage(doc, mode) {
    if (mode === 'selection') {
        const sel = doc.getSelection && doc.getSelection();
        return ((sel && sel.toString()) || '').trim();
    }
    const main = doc.querySelector('article, main') || doc.body;
    return readableText(main).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}
