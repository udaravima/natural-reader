import { describe, it, expect } from 'vitest';
import { applyEvent } from './chatEvents';
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

    it('shows "executing tool…" between the tool input and the next step', () => {
        const events = loadFixture('tool_round');
        const upto = events.findIndex((e) => e.type === 'tool-input-available') + 1;
        expect(reduce(events.slice(0, upto)).toolStatus).toBe('executing tool…');
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
