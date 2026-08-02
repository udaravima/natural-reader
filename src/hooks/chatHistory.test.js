import { describe, it, expect } from 'vitest';
import { buildApiMessage, buildChatHistory } from './chatHistory';

describe('buildApiMessage', () => {
    it('maps role + content, defaulting missing content to an empty string', () => {
        expect(buildApiMessage({ role: 'user', content: 'hi' })).toEqual({ role: 'user', content: 'hi' });
        expect(buildApiMessage({ role: 'assistant' })).toEqual({ role: 'assistant', content: '' });
    });

    it('collects image + audio base64 into `images`, and omits it when there are none', () => {
        const withImg = buildApiMessage({
            role: 'user',
            content: 'look',
            attachments: [
                { kind: 'image', base64: 'IMG' },
                { kind: 'audio', base64: 'AUD' },
                { kind: 'image' }, // no base64 → skipped (e.g. stripped on session save)
            ],
        });
        expect(withImg).toEqual({ role: 'user', content: 'look', images: ['IMG', 'AUD'] });

        const noImg = buildApiMessage({ role: 'user', content: 'text only', attachments: [] });
        expect(noImg).not.toHaveProperty('images');
    });
});

describe('buildChatHistory', () => {
    const excerpt = { role: 'system', content: 'Relevant excerpt: """the moon is made of cheese"""' };
    const currentUser = { role: 'user', content: 'summarize this' };

    it('places the excerpt immediately before the current user message on a FRESH chat', () => {
        const history = buildChatHistory({
            priorMessages: [],
            contextPreamble: [excerpt],
            userMsg: currentUser,
        });
        expect(history).toEqual([
            { role: 'system', content: excerpt.content },
            { role: 'user', content: 'summarize this' },
        ]);
    });

    it('keeps the excerpt ADJACENT to the current user message on a MULTI-TURN chat (regression)', () => {
        // The bug: the excerpt used to be prepended to the front of the whole
        // history, so on an existing conversation it was buried behind prior
        // turns and the model reported "no content attached". It must sit right
        // before the latest user message instead.
        const prior = [
            { role: 'user', content: 'q1' },
            { role: 'assistant', content: 'a1' },
            { role: 'user', content: 'q2' },
            { role: 'assistant', content: 'a2' },
        ];
        const history = buildChatHistory({
            priorMessages: prior,
            contextPreamble: [excerpt],
            userMsg: currentUser,
        });

        // Last message is the current user turn; the excerpt is the one right before it.
        expect(history[history.length - 1]).toEqual({ role: 'user', content: 'summarize this' });
        expect(history[history.length - 2]).toEqual({ role: 'system', content: excerpt.content });

        // And it is NOT stranded at the front behind the prior conversation.
        expect(history[0]).toEqual({ role: 'user', content: 'q1' });
        expect(history.filter(m => m.role === 'system')).toHaveLength(1);
    });

    it('is a plain prior-messages + user history when there is no context preamble', () => {
        const prior = [{ role: 'user', content: 'q1' }, { role: 'assistant', content: 'a1' }];
        const history = buildChatHistory({ priorMessages: prior, contextPreamble: [], userMsg: currentUser });
        expect(history).toEqual([
            { role: 'user', content: 'q1' },
            { role: 'assistant', content: 'a1' },
            { role: 'user', content: 'summarize this' },
        ]);
    });

    it('preserves order of multiple preamble blocks (excerpt then retrieved passages)', () => {
        const retrieved = { role: 'system', content: 'Additional passages: [1] ...' };
        const history = buildChatHistory({
            priorMessages: [{ role: 'user', content: 'q1' }],
            contextPreamble: [excerpt, retrieved],
            userMsg: currentUser,
        });
        expect(history.map(m => m.role)).toEqual(['user', 'system', 'system', 'user']);
        expect(history[history.length - 2].content).toBe(retrieved.content);
        expect(history[history.length - 3].content).toBe(excerpt.content);
    });
});
