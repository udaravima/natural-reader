/**
 * The chat turn transport (C1 spec §6, §7.1): POST the new message and read
 * the server-sent events back. Each event is one `data: {json}` line plus a
 * blank line; `data: [DONE]` ends the stream.
 */
import { apiFetch } from '../utils/apiFetch';

export const turnPath = (sessionId) => `/v1/chat/sessions/${encodeURIComponent(sessionId)}/turns`;

export function postTurn({ apiHost, apiPort, sessionId, body, signal }) {
    return apiFetch(apiHost, apiPort, turnPath(sessionId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body),
        signal,
    });
}

const dataOf = (frame) => frame.split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).replace(/^ /, ''))
    .join('\n');

// Yields parsed events; returns at [DONE]. An aborted fetch rejects the read
// with an AbortError, which propagates to the caller.
export async function* readEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
        for (;;) {
            const { value, done } = await reader.read();
            if (done) return;
            buf += decoder.decode(value, { stream: true });
            buf = buf.replace(/\r\n/g, '\n');
            let cut;
            while ((cut = buf.indexOf('\n\n')) !== -1) {
                const data = dataOf(buf.slice(0, cut));
                buf = buf.slice(cut + 2);
                if (!data) continue;
                if (data === '[DONE]') return;
                try {
                    yield JSON.parse(data);
                } catch {
                    // a malformed frame is skipped, not fatal
                }
            }
        }
    } finally {
        try { reader.releaseLock(); } catch { /* a read still pending after an abort */ }
    }
}
