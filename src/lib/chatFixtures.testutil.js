// Test-only: the backend's contract fixtures (server/tests/fixtures/chat_events,
// spec §11) and a fake streamed body. Imported by *.test.js files only.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const currentDir = dirname(fileURLToPath(import.meta.url));
const fixturesDir = resolve(currentDir, '../../server/tests/fixtures/chat_events');

export const loadFixture = (name) =>
    readFileSync(resolve(fixturesDir, `${name}.jsonl`), 'utf8')
        .trim().split('\n').map((line) => JSON.parse(line));

// A ReadableStream-like body that hands out `text` in tiny chunks, so frames
// (and multi-byte characters) split across reads the way real networks split them.
export const bodyOf = (text, chunkSize = 7) => {
    const bytes = new TextEncoder().encode(text);
    let i = 0;
    return {
        getReader: () => ({
            read: async () => {
                if (i >= bytes.length) return { done: true, value: undefined };
                const value = bytes.slice(i, i + chunkSize);
                i += chunkSize;
                return { done: false, value };
            },
            releaseLock() {},
        }),
    };
};

export const sseText = (events, extra = '') =>
    events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') + 'data: [DONE]\n\n' + extra;
