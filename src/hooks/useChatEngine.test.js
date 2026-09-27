import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChatEngine } from './useChatEngine';
import { INFERENCE_DEFAULTS } from './inference';
import { postTurn } from '../lib/chatStream';
import { apiFetch } from '../utils/apiFetch';
import { bodyOf, loadFixture, sseText } from '../lib/chatFixtures.testutil';

vi.mock('../lib/chatStream', async (importOriginal) => ({ ...(await importOriginal()), postTurn: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));

// vi.hoisted: vi.mock factories run before this file's own top-level code.
const store = vi.hoisted(() => ({
    getRecentSessions: vi.fn(async () => []),
    getSession: vi.fn(async () => null),
    deleteSession: vi.fn(async () => true),
    renameSession: vi.fn(async () => true),
    updateSessionPins: vi.fn(async () => true),
    importLegacy: vi.fn(async () => 's-imported'),
    isLocalId: vi.fn(() => false),
}));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: () => store }));

const MODELS = [{ id: 'ollama:m', provider: 'ollama', kind: 'ollama', name: 'm', capabilities: {} }];
const ok = (events) => ({ ok: true, status: 200, body: bodyOf(sseText(events)), clone() { return this; } });
const refusal = (status, detail) => ({ ok: false, status, json: async () => ({ detail }), clone() { return this; } });

const baseProps = (over = {}) => ({
    selectedModel: 'ollama:m', chatTtsMode: 'after-complete', chatAutoTts: false,
    inference: { ...INFERENCE_DEFAULTS, think: 'low' }, isLocalhost: false, selectedVoice: 'af_heart',
    playbackSpeed: 1, requestTimeout: 15, apiHost: '', apiPort: '8000', currentDocId: 'a'.repeat(64),
    synthesizeText: vi.fn(), playChatUrl: vi.fn(), playChatSpeech: vi.fn(), stopChatPlayback: vi.fn(),
    showToast: vi.fn(), ...over,
});

beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ models: MODELS, budget: null }) });
});
afterEach(() => vi.useRealTimers());

describe('useChatEngine — turns', () => {
    it('posts one turn, adopts the server ids and renders the streamed reply', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        let out;
        await act(async () => { out = await result.current.sendMessage('Hi'); });
        expect(out).toEqual({ sent: true });
        const [{ sessionId, body }] = postTurn.mock.calls[0];
        expect(sessionId).toMatch(/^s-/);
        expect(body).toMatchObject({
            message: { content: 'Hi', attachments: [] }, model: 'ollama:m',
            settings: { think: 'low', num_ctx: null, keep_alive: null, num_predict: null },
            context: { doc_id: 'a'.repeat(64) }, session: { pins: [] },
        });
        const [user, assistant] = result.current.messages;
        expect(user.id).toBe('<user-message-id>');
        expect(assistant).toMatchObject({ id: '<message-id>', content: 'Hello there.', status: 'complete' });
        expect(store.getSession).toHaveBeenCalled();          // the server-written log is re-read
    });

    it('sends no `session` block when the chat already exists', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.sendMessage('one'); });
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        await act(async () => { await result.current.sendMessage('two'); });
        expect(postTurn.mock.calls[1][0].body.session).toBeUndefined();
    });

    it('a refusal removes the optimistic messages and gives the text back', async () => {
        postTurn.mockResolvedValue(refusal(409, { error: 'turn_in_progress', message: 'A reply is still being written in this chat.' }));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        let out;
        await act(async () => { out = await result.current.sendMessage('Hi'); });
        expect(out).toMatchObject({ sent: false, refused: true, text: 'Hi' });
        expect(result.current.messages).toEqual([]);
        expect(props.showToast).toHaveBeenCalledWith('A reply is still being written in this chat.', 5000);
    });

    it('a 429 shows the budget notice and never streams', async () => {
        postTurn.mockResolvedValue(refusal(429, { error: 'budget_exhausted', remaining_tokens: 0, reset_at: '2026-09-28T00:00:00Z' }));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(result.current.inferenceBudget).toMatchObject({ remaining_tokens: 0 });
        expect(props.showToast.mock.calls[0][0]).toMatch(/budget exhausted/);
    });

    it('Stop marks the reply aborted and keeps the partial text', async () => {
        const events = loadFixture('plain').filter((e) => !['text-end', 'finish-step', 'finish'].includes(e.type));
        const abortable = { ok: true, status: 200, clone() { return this; }, body: {
            getReader: () => {
                const inner = bodyOf(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')).getReader();
                return { read: async () => { const r = await inner.read(); if (r.done) { const err = new Error('aborted'); err.name = 'AbortError'; throw err; } return r; }, releaseLock() {} };
            } } };
        postTurn.mockResolvedValue(abortable);
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(result.current.messages[1]).toMatchObject({ status: 'aborted', content: 'Hello there.' });
    });

    it('continuing a legacy browser-only chat imports it first', async () => {
        store.isLocalId.mockImplementation((id) => id === 's-legacy');
        store.getSession.mockResolvedValueOnce({ id: 's-legacy', messages: [], events: [], pins: [] });
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-legacy'); });
        await act(async () => { await result.current.sendMessage('continue'); });
        expect(store.importLegacy).toHaveBeenCalledWith('s-legacy');
        expect(postTurn.mock.calls[0][0].sessionId).toBe('s-imported');
        store.isLocalId.mockImplementation(() => false);
    });
});

describe('useChatEngine — models and reload', () => {
    it('keeps the model objects and the budget from /v1/inference/models', async () => {
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await waitFor(() => expect(result.current.availableModels).toEqual(MODELS));
        expect(result.current.reachable).toBe(true);
    });

    it('polls a reloaded chat whose reply is still streaming until it settles', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        store.getSession
            .mockResolvedValueOnce({ id: 's-1', messages: [{ id: 'a1', role: 'assistant', content: 'Par', status: 'streaming' }], events: [], pins: [] })
            .mockResolvedValue({ id: 's-1', messages: [{ id: 'a1', role: 'assistant', content: 'Partial done', status: 'complete' }], events: [], pins: [] });
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-1'); });
        expect(result.current.messages[0].status).toBe('streaming');
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(result.current.messages[0]).toMatchObject({ status: 'complete', content: 'Partial done' });
    });

    // Added beyond the brief (controller note 3): a connection that drops before
    // `finish`/[DONE] must not look complete — it stays 'streaming', and the
    // poll above re-reads the server until the saved reply settles.
    it('a stream cut before [DONE] stays streaming and is re-read from the server', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const cut = loadFixture('plain').filter((e) => !['text-end', 'finish-step', 'finish'].includes(e.type));
        postTurn.mockResolvedValue({ ok: true, status: 200, clone() { return this; },
            body: bodyOf(cut.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')) });
        store.getSession.mockResolvedValue({ id: 'x', events: [], pins: [], messages: [
            { id: '<user-message-id>', role: 'user', content: 'Hi' },
            { id: '<message-id>', role: 'assistant', content: 'Hello there. And more.', status: 'complete' }] });
        const { result } = renderHook(() => useChatEngine(baseProps()));
        let out;
        await act(async () => { out = await result.current.sendMessage('Hi'); });
        expect(out).toEqual({ sent: true });
        expect(result.current.isStreaming).toBe(false);
        expect(result.current.messages[1]).toMatchObject({ id: '<message-id>', status: 'streaming', content: 'Hello there.' });
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(result.current.messages[1]).toMatchObject({ status: 'complete', content: 'Hello there. And more.' });
    });
});
