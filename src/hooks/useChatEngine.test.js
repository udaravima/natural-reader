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
// The options the hook passed to makeSessionStore (its onBackendOffline hook).
const storeOpts = vi.hoisted(() => ({ current: null }));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: (opts) => { storeOpts.current = opts; return store; } }));

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
    // Reset (not just clear): a test's mockImplementation / leftover *Once
    // values must not leak into the next test. vi.fn(impl) resets to `impl`.
    vi.resetAllMocks();
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

    // Fix round 1 (controller ruling): the server reads `session.pins` only when
    // the turn creates the chat, so sending them every time is harmless — and a
    // first send that failed on the network no longer loses them on the retry.
    it('sends the pins with every turn, so a retried first send still carries them', async () => {
        const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
        const pin = { id: 'p1', doc_id: 'd', fileName: 'f.md', page: 1, kind: 'selection', text: 'cheese' };
        postTurn.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        act(() => { result.current.addPin(pin); });
        await act(async () => { await result.current.sendMessage('one'); });
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        await act(async () => { await result.current.sendMessage('two'); });
        expect(postTurn.mock.calls[1][0].sessionId).toBe(postTurn.mock.calls[0][0].sessionId);
        expect(postTurn.mock.calls[1][0].body.session).toEqual({ pins: [pin] });
        errSpy.mockRestore();
    });

    it('a refusal removes the optimistic messages and gives the text back', async () => {
        postTurn.mockResolvedValue(refusal(409, { error: 'turn_in_progress', message: 'A reply is still being written in this chat.' }));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        let out;
        await act(async () => { out = await result.current.sendMessage('Hi'); });
        expect(out).toMatchObject({ sent: false, refused: true, text: 'Hi' });
        expect(result.current.messages).toEqual([]);
        expect(result.current.activeSessionId).toBeNull();   // the refused NEW chat never existed
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

    it('Stop aborts the request and marks the reply aborted, keeping the partial text', async () => {
        const events = loadFixture('plain').filter((e) => !['text-end', 'finish-step', 'finish'].includes(e.type));
        // A body that hands out the partial reply, then blocks like a live
        // stream until the request's signal is aborted.
        postTurn.mockImplementation(async ({ signal }) => ({ ok: true, status: 200, clone() { return this; }, body: {
            getReader: () => {
                const inner = bodyOf(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')).getReader();
                return {
                    read: async () => {
                        const r = await inner.read();
                        if (!r.done) return r;
                        await new Promise((resolve) => signal.addEventListener('abort', resolve));
                        const err = new Error('aborted'); err.name = 'AbortError'; throw err;
                    },
                    releaseLock() {},
                };
            } } }));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        let sending;
        act(() => { sending = result.current.sendMessage('Hi'); });
        await waitFor(() => expect(result.current.messages[1]?.content).toBe('Hello there.'));
        await act(async () => { result.current.stopStream(); await sending; });
        expect(postTurn.mock.calls[0][0].signal.aborted).toBe(true);
        expect(result.current.messages[1]).toMatchObject({ status: 'aborted', content: 'Hello there.' });
        expect(result.current.isStreaming).toBe(false);
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
        expect(result.current.activeSessionId).toBe('s-imported');
    });

    it('a second Send while a legacy chat is being copied does not import it twice', async () => {
        store.isLocalId.mockImplementation((id) => id === 's-legacy');
        store.getSession.mockResolvedValueOnce({ id: 's-legacy', messages: [], events: [], pins: [] });
        let finishImport;
        store.importLegacy.mockImplementationOnce(() => new Promise((resolve) => { finishImport = resolve; }));
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-legacy'); });
        let first; let second;
        act(() => { first = result.current.sendMessage('one'); second = result.current.sendMessage('two'); });
        expect(await second).toEqual({ sent: false });
        await act(async () => { finishImport('s-imported'); await first; });
        expect(store.importLegacy).toHaveBeenCalledTimes(1);
        expect(postTurn).toHaveBeenCalledTimes(1);
    });

    it('switching chats while a legacy chat is being copied does not hijack the new view', async () => {
        store.isLocalId.mockImplementation((id) => id === 's-legacy');
        store.getSession
            .mockResolvedValueOnce({ id: 's-legacy', messages: [], events: [], pins: [] })
            .mockResolvedValueOnce({ id: 's-other', messages: [], events: [], pins: [] });
        let finishImport;
        store.importLegacy.mockImplementationOnce(() => new Promise((resolve) => { finishImport = resolve; }));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-legacy'); });
        let sending;
        act(() => { sending = result.current.sendMessage('one'); });
        await act(async () => { await result.current.switchToSession('s-other'); });
        let out;
        await act(async () => { finishImport('s-imported'); out = await sending; });
        expect(out).toMatchObject({ sent: false, refused: true, text: 'one' });
        expect(result.current.activeSessionId).toBe('s-other');
        expect(postTurn).not.toHaveBeenCalled();
    });

    it('an imported chat shows in the sidebar even when its first turn is refused', async () => {
        store.isLocalId.mockImplementation((id) => id === 's-legacy');
        store.getSession.mockResolvedValueOnce({ id: 's-legacy', messages: [], events: [], pins: [] });
        postTurn.mockResolvedValue(refusal(409, { error: 'turn_in_progress', message: 'Busy.' }));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-legacy'); });
        const listReads = store.getRecentSessions.mock.calls.length;
        await act(async () => { await result.current.sendMessage('continue'); });
        expect(store.getRecentSessions.mock.calls.length).toBeGreaterThan(listReads);
        expect(result.current.activeSessionId).toBe('s-imported');
    });

    it('an error event keeps the partial text, toasts, and clears the read-aloud indicator', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('error')));
        const props = baseProps({ chatAutoTts: true });
        const { result } = renderHook(() => useChatEngine(props));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(result.current.messages[1]).toMatchObject({ status: 'error', content: 'Partial' });
        expect(props.showToast).toHaveBeenCalledWith('Chat failed: The model provider returned an error: out of memory', 5000);
        await waitFor(() => expect(result.current.speakingMessageId).toBeNull());
    });

    it('a budget_exhausted error event toasts and re-reads the budget', async () => {
        const events = loadFixture('error').map((e) => (e.type === 'error'
            ? { ...e, code: 'budget_exhausted', message: 'Daily token budget exhausted.' } : e));
        postTurn.mockResolvedValue(ok(events));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));   // the debounced first model read
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(props.showToast).toHaveBeenCalledWith('Daily inference budget exhausted.', 6000);
        expect(apiFetch).toHaveBeenCalledTimes(2);
        expect(result.current.messages[1]).toMatchObject({ status: 'error', content: 'Partial' });
    });
});

describe('useChatEngine — per-provider settings', () => {
    // Final review M4: num_ctx / keep_alive are Ollama options; for an
    // OpenAI-kind model they are not sent, whatever the stored settings say.
    it.each([
        ['ollama:m', { num_ctx: 8192, keep_alive: -1, num_predict: 256 }],
        ['cloud:x', { num_ctx: null, keep_alive: null, num_predict: 256 }],
    ])('sends only the knobs %s honours', async (model, want) => {
        const models = [...MODELS, { id: 'cloud:x', provider: 'cloud', kind: 'openai', name: 'x', capabilities: {} }];
        apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ models, budget: null }) });
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const props = baseProps({ selectedModel: model,
            inference: { ...INFERENCE_DEFAULTS, numCtx: 8192, keepAlive: -1, numPredict: 256 } });
        const { result } = renderHook(() => useChatEngine(props));
        await waitFor(() => expect(result.current.availableModels).toEqual(models));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(postTurn.mock.calls[0][0].body.settings).toMatchObject(want);
    });
});

describe('useChatEngine — models and reload', () => {
    it('keeps the model objects and the budget from /v1/inference/models', async () => {
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await waitFor(() => expect(result.current.availableModels).toEqual(MODELS));
        expect(result.current.reachable).toBe(true);
    });

    it('warns once per offline streak, and again after the server is back', async () => {
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        await waitFor(() => expect(result.current.reachable).toBe(true));
        const offlineToasts = () => props.showToast.mock.calls.filter(([m]) => /offline/i.test(m)).length;
        act(() => { storeOpts.current.onBackendOffline(); storeOpts.current.onBackendOffline(); });
        expect(offlineToasts()).toBe(1);
        expect(props.showToast).toHaveBeenCalledWith("Chat is offline — can't reach the server.", 4000);
        await act(async () => { await result.current.refreshModels(); });   // the server answers again
        act(() => { storeOpts.current.onBackendOffline(); });
        expect(offlineToasts()).toBe(2);
    });

    it('a poll tick still in flight when a new send starts does not wipe the local turn', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const remote = { id: 's-1', messages: [{ id: 'a1', role: 'assistant', content: 'Par', status: 'streaming' }], events: [], pins: [] };
        let finishTick;
        store.getSession
            .mockResolvedValueOnce(remote)
            .mockImplementationOnce(() => new Promise((resolve) => { finishTick = resolve; }));
        let finishTurn;
        postTurn.mockImplementation(() => new Promise((resolve) => { finishTurn = resolve; }));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-1'); });
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });   // the tick is now awaiting the server
        expect(finishTick).toBeTypeOf('function');
        let sending;
        act(() => { sending = result.current.sendMessage('next'); });
        await act(async () => { finishTick(remote); });
        expect(result.current.messages.map((m) => m.content)).toContain('next');
        await act(async () => { finishTurn(ok(loadFixture('plain'))); await sending; });
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

describe('useChatEngine — "Use this document" (v2.4 Task E)', () => {
    beforeEach(() => localStorage.clear());

    it('a chat with the document switched off sends no document, and keeps it off', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps({ userId: 'u1' })));
        act(() => result.current.docUse.setEnabled(false));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(postTurn.mock.calls[0][0].body.context.doc_id).toBeNull();
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        await act(async () => { await result.current.sendMessage('Again'); });
        expect(postTurn.mock.calls[1][0].body.context.doc_id).toBeNull();
        expect(result.current.docUse.enabled).toBe(false);
    });

    it('sends the open document by default', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps({ userId: 'u1' })));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(postTurn.mock.calls[0][0].body.context.doc_id).toBe('a'.repeat(64));
    });
});
