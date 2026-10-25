import { describe, it, expect } from 'vitest';
import { applyEvent, toolStatusFor } from './chatEvents';
import { readEvents } from './chatStream';
import { bodyOf, loadFixture, sseText } from './chatFixtures.testutil';

const fresh = () => ({ id: 'a-local', role: 'assistant', content: '', thinking: '', status: 'streaming' });
const reduce = (events) => events.reduce(applyEvent, fresh());

describe('applyEvent over the backend contract fixtures', () => {
    it('plain: text and a completed status with stats', () => {
        const m = reduce(loadFixture('plain'));
        expect(m.content).toBe('Hello there.');
        expect(m.status).toBe('complete');
        expect(m.finishReason).toBe('stop');
        expect(m.stats.model).toBe('ollama:m');
        expect(m.id).toBe('<message-id>');
    });

    it('tool_round: a settled tool call and the answer', () => {
        const m = reduce(loadFixture('tool_round'));
        expect(m.toolCalls).toEqual([{
            id: 'c1', name: 'web_search', arguments: { query: 'news' },
            result_summary: { ok: true, chunk_count: null, query: 'news', summary_text: 'Web search for "news" returned 1 result(s).' },
        }]);
        expect(m.toolStatus).toBeUndefined();
        // Final review M5: each step's text is its own paragraph, exactly as
        // the server saves it (orchestrator.py joins steps the same way).
        expect(m.content).toBe('Let me search.\n\nHere is the news.');
    });

    it('adds no separator before the first text, or when earlier steps said nothing', () => {
        const events = loadFixture('tool_round').filter((e) => e.id !== 't1');
        expect(reduce(events).content).toBe('Here is the news.');
    });

    it('says what the tool is doing between the tool input and the next step', () => {
        const events = loadFixture('tool_round');
        const upto = events.findIndex((e) => e.type === 'tool-input-available') + 1;
        expect(reduce(events.slice(0, upto)).toolStatus).toBe('Searching the web…');
    });

    it('a later round says "Still searching… (round n)"', () => {
        expect(toolStatusFor('search_documents', 1)).toBe('Searching the document…');
        expect(toolStatusFor('read_document_pages', 1)).toBe('Reading the document…');
        expect(toolStatusFor('search_documents', 2)).toBe('Still searching… (round 2)');
        expect(toolStatusFor('web_search', 3)).toBe('Still searching… (round 3)');
        expect(toolStatusFor('some_new_tool', undefined)).toBe('Running a tool…');
        const m = applyEvent({}, { type: 'tool-input-available', toolCallId: 'c', toolName: 'search_documents', round: 2 });
        expect(m.toolStatus).toBe('Still searching… (round 2)');
    });

    it('error: keeps the partial reply and records the error', () => {
        const m = reduce(loadFixture('error'));
        expect(m.status).toBe('error');
        expect(m.error.code).toBe('provider_error');
        expect(m.content).toBe('Partial');
    });

    it('notice and context: surfaced on the message', () => {
        expect(reduce(loadFixture('notice')).notices).toEqual(['This model rejected tools — answered without them.']);
        expect(reduce(loadFixture('context')).docContext.notes[0]).toMatchObject({ kind: 'prefetch', docName: 'Thesis.pdf' });
    });

    it('ignores unknown event types (forward compatible)', () => {
        expect(applyEvent(fresh(), { type: 'data-something-new', x: 1 })).toEqual(fresh());
    });

    it('is the same whether events come from the file or through the SSE reader', async () => {
        const events = loadFixture('tool_round');
        let m = fresh();
        for await (const ev of readEvents(bodyOf(sseText(events)))) m = applyEvent(m, ev);
        expect(m).toEqual(reduce(events));
    });
});

describe('data-sources (v2.4 Task D)', () => {
    const ev = { type: 'data-sources', docId: 'd', docName: 'T.pdf', pages: [3, 8], used: true };
    const note = { kind: 'sources', docId: 'd', docName: 'T.pdf', pages: [3, 8], used: true };

    it('appends a sources note to the reply\'s document notes', () => {
        const m = applyEvent({ docContext: { notes: [{ kind: 'prefetch' }] } }, ev);
        expect(m.docContext.notes).toEqual([{ kind: 'prefetch' }, note]);
    });

    it('starts the notes when the reply had none', () => {
        expect(applyEvent({}, ev).docContext.notes).toEqual([note]);
    });
});

describe('tool status for the project tools (C2.1)', () => {
    it.each([
        ['extract_from_documents', 'Reading pages…'],
        ['document_outline', 'Reading an outline…'],
        ['read_web_pages', 'Reading web pages…'],
        ['offer_web_search', 'Offering a web search…'],
    ])('%s says "%s"', (toolName, label) => {
        expect(toolStatusFor(toolName, 1)).toBe(label);
        expect(applyEvent({}, { type: 'tool-input-available', toolCallId: 'c', toolName, round: 1 }).toolStatus).toBe(label);
        expect(toolStatusFor(toolName, 2)).toBe('Still searching… (round 2)');
    });
});

describe('data-sources for a project reply (C2.1)', () => {
    const documents = [
        { docId: 'a', docName: 'Guide.md', pages: [1, 3], used: true },
        { docId: 'b', docName: 'Notes.txt', pages: [2], used: false },
    ];

    it('saves the per-document shape as a sources note', () => {
        const m = applyEvent({ docContext: { notes: [{ kind: 'scope', documents: [] }] } }, { type: 'data-sources', documents });
        expect(m.docContext.notes).toEqual([{ kind: 'scope', documents: [] }, { kind: 'sources', documents }]);
    });

    it('still saves the document-scope shape the old way', () => {
        const m = applyEvent({}, { type: 'data-sources', docId: 'd', docName: 'T.pdf', pages: [3], used: true });
        expect(m.docContext.notes).toEqual([{ kind: 'sources', docId: 'd', docName: 'T.pdf', pages: [3], used: true }]);
    });

    it('takes a malformed documents field for the old shape rather than throwing', () => {
        const m = applyEvent({}, { type: 'data-sources', documents: 'oops' });
        expect(m.docContext.notes[0]).toMatchObject({ kind: 'sources', pages: [], used: false });
        expect(m.docContext.notes[0].documents).toBeUndefined();
    });
});

describe('web notes (C2.1)', () => {
    it('data-web-sources adds a web_sources note', () => {
        const pages = [{ n: 1, title: 'A page', url: 'https://www.example.com/a' }];
        const m = applyEvent({ docContext: { notes: [{ kind: 'prefetch' }] } }, { type: 'data-web-sources', pages });
        expect(m.docContext.notes).toEqual([{ kind: 'prefetch' }, { kind: 'web_sources', pages }]);
        expect(applyEvent({}, { type: 'data-web-sources' }).docContext.notes).toEqual([{ kind: 'web_sources', pages: [] }]);
    });

    it('data-web-offer adds a web_offer note and changes nothing else', () => {
        const before = { id: 'm', content: 'Not in the documents.', status: 'streaming' };
        const m = applyEvent(before, { type: 'data-web-offer', query: 'zephyr 9 release', reason: 'The files are silent on it.' });
        expect(m.docContext.notes).toEqual([{ kind: 'web_offer', query: 'zephyr 9 release', reason: 'The files are silent on it.' }]);
        expect(m.status).toBe('streaming'); // the reply is still to finish, as the server sends `finish` next
        expect(m.error).toBeUndefined();
        expect(m.content).toBe(before.content);
    });

    it('a reply with an offer ends complete, not errored or streaming', () => {
        const events = [
            { type: 'start', messageId: 'srv' },
            { type: 'tool-input-available', toolCallId: 'c', toolName: 'offer_web_search', round: 1, input: { query: 'q', reason: 'r' } },
            { type: 'tool-output-available', toolCallId: 'c', output: { ok: true, summary_text: 'Offered.' } },
            { type: 'data-web-offer', query: 'q', reason: 'r' },
            { type: 'text-delta', delta: 'I could not find it.' },
            { type: 'finish', finishReason: 'stop' },
        ];
        const m = reduce(events);
        expect(m.status).toBe('complete');
        expect(m.error).toBeUndefined();
        expect(m.toolStatus).toBeUndefined();
        expect(m.docContext.notes).toEqual([{ kind: 'web_offer', query: 'q', reason: 'r' }]);
    });
});
