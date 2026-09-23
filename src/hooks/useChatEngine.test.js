import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChatEngine } from './useChatEngine';
import { INFERENCE_DEFAULTS } from './inference';
import { chatFetch } from '../lib/chatTransport';

// The transport seam: the hook must never touch a real network. chatFetch is
// the single gateway to both /v1 (server) and /api (local) inference, so
// mocking it pins the full request sequence — which is exactly what these
// tests assert: the ORDER and CONTENT of the fallback retries.
vi.mock('../lib/chatTransport', () => ({
    CHAT_PATH: { server: '/v1/inference/chat', local: '/api/chat' },
    MODELS_PATH: { server: '/v1/inference/models', local: '/api/tags' },
    chatFetch: vi.fn(),
    // Faithful to the real contract: only a 429 carries budget detail.
    budgetDetail: vi.fn(async (res) =>
        res?.status === 429
            ? { remaining_tokens: 0, reset_at: '2026-09-18T00:00:00Z', status: 'budget_exhausted' }
            : null),
    formatResetAt: (iso) => (Number.isNaN(new Date(iso).getTime()) ? '' : '12:00 AM'),
}));

// Sessions ride IndexedDB/HTTP in real life; none of that matters to retry
// ordering. saveSession resolves null (= "not saved", the offline branch).
vi.mock('../lib/sessionStore', () => ({
    makeSessionStore: () => ({
        getRecentSessions: async () => [],
        getSession: async () => null,
        saveSession: async () => null,
        deleteSession: async () => true,
        renameSession: async () => true,
        updateSessionPins: async () => {},
    }),
}));

// Tool registry: gate exactly like the real one — tools exist only when an
// indexed document is loaded. The definitions are arbitrary; what matters is
// that `tools` survives (or drops) in the retried request bodies.
vi.mock('../lib/chatTools', () => ({
    getToolDefinitions: vi.fn((ctx) =>
        ctx.currentDocId && ctx.currentDocIndexState === 'indexed'
            ? [{ type: 'function', function: { name: 'search_document' } }]
            : []),
    executeToolCall: vi.fn(async () => 'tool result'),
}));

const TOOLS = [{ type: 'function', function: { name: 'search_document' } }];

// --- response fixtures ---------------------------------------------------

// A fake 200 Response whose body streams the given NDJSON payloads in one
// chunk. The hook only needs ok/status/body.getReader(); the final payload
// must carry done:true so consumeStream() stops and records stats.
const streamResponse = (payloads, { status = 200 } = {}) => {
    const bytes = new TextEncoder()
        .encode(payloads.map((p) => JSON.stringify(p)).join('\n') + '\n');
    let consumed = false;
    return {
        ok: status >= 200 && status < 300,
        status,
        body: {
            getReader: () => ({
                read: async () => {
                    if (consumed) return { value: undefined, done: true };
                    consumed = true;
                    return { value: bytes, done: false };
                },
            }),
        },
    };
};

const finalChunk = { message: { role: 'assistant', content: 'ok.' }, done: true, done_reason: 'stop' };
const errorResponse = (status) => ({ ok: false, status });

// --- hook harness ---------------------------------------------------------

const baseProps = (over = {}) => ({
    ollamaHost: '', ollamaPort: '11434',
    inferenceSource: 'server',
    selectedModel: 'qwen3.5:latest',
    chatTtsMode: 'after-complete',   // no per-token TTS during the stream
    chatAutoTts: false,              // no TTS at all — keeps the queue idle
    inference: { ...INFERENCE_DEFAULTS },
    onInferencePersist: vi.fn(),
    isLocalhost: false,
    selectedVoice: 'af_heart',
    playbackSpeed: 1,
    requestTimeout: 15,
    apiHost: '', apiPort: '8000',
    currentDocId: 'doc-abc',          // an indexed doc → search_document exists
    currentDocIndexState: 'indexed',
    synthesizeText: vi.fn(async () => null),
    playChatUrl: vi.fn(async () => {}),
    playChatSpeech: vi.fn(async () => {}),
    stopChatPlayback: vi.fn(),
    showToast: vi.fn(),
    ...over,
});

// A chatFetch mock that records bodies then delegates to per-test logic.
const chatFetchMock = (decide) => {
    const state = { bodies: [], calls: 0 };
    chatFetch.mockImplementation(async (_src, _hosts, _path, opts) => {
        state.calls += 1;
        state.bodies.push(JSON.parse(opts.body));
        return decide(state.calls);
    });
    return state;
};

// Render + send, with the transport decided by the test.
const sendWith = async (props, decide) => {
    const state = chatFetchMock(decide);
    const { result } = renderHook(() => useChatEngine(props));
    await act(async () => { await result.current.sendMessage('hello'); });
    await act(async () => {});
    return { result, ...state };
};

describe('useChatEngine sendMessage fallback-retry sequencing', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(console, 'error').mockImplementation(() => {});
        vi.spyOn(console, 'warn').mockImplementation(() => {});
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('retries a rejected think level FIRST and keeps tools on the retry', async () => {
        const onInferencePersist = vi.fn();
        const { result, bodies, calls } = await sendWith(
            baseProps({ onInferencePersist, inference: { ...INFERENCE_DEFAULTS, think: 'low' } }),
            (n) => (n === 1 ? errorResponse(400) : streamResponse([finalChunk])),
        );
        expect(calls).toBe(2);
        expect(bodies[0].think).toBe('low');
        expect(bodies[0].tools).toEqual(TOOLS);
        // THE regression guard: the think retry must carry the same tools the
        // first request had. The old hand-rolled retry silently dropped
        // search_document and the model answered from memory while a toast
        // blamed the wrong cause.
        expect(bodies[1].think).toBe(true);
        expect(bodies[1].tools).toEqual(TOOLS);
        // Self-heal: the rejected level is persisted so the sidebar stops
        // claiming it is active and the model stops paying a wasted trip.
        expect(onInferencePersist).toHaveBeenCalledWith({ think: 'on' });
        expect(result.current.messages.at(-1).content).toBe('ok.');
    });

    it('drops tools only when tools are the problem, preserving think', async () => {
        const onInferencePersist = vi.fn();
        const { bodies, calls } = await sendWith(
            baseProps({ onInferencePersist, inference: { ...INFERENCE_DEFAULTS, think: 'off' } }),
            (n) => (n === 1 ? errorResponse(400) : streamResponse([finalChunk])),
        );
        expect(calls).toBe(2);
        expect(bodies[0].think).toBe(false);
        expect(bodies[0].tools).toEqual(TOOLS);
        // The tools-drop retry keeps every inference field — dropping them
        // too would silently change the model's behaviour.
        expect(bodies[1].tools).toBeUndefined();
        expect(bodies[1].think).toBe(false);
        expect(onInferencePersist).not.toHaveBeenCalled();
    });

    it('walks the full chain in order: level downgrade first, then tools drop', async () => {
        const { result, bodies, calls } = await sendWith(
            baseProps({ inference: { ...INFERENCE_DEFAULTS, think: 'high' } }),
            (n) => (n < 3 ? errorResponse(400) : streamResponse([finalChunk])),
        );
        expect(calls).toBe(3);
        expect(bodies[0]).toMatchObject({ think: 'high' });
        expect(bodies[0].tools).toEqual(TOOLS);
        expect(bodies[1]).toMatchObject({ think: true });
        expect(bodies[1].tools).toEqual(TOOLS);
        // The tools-drop retry must NOT revert the think fix that already
        // worked — it inherits think:true from the override, not the
        // original rejected level.
        expect(bodies[2].think).toBe(true);
        expect(bodies[2].tools).toBeUndefined();
        expect(result.current.messages.at(-1).content).toBe('ok.');
    });

    it('does not retry at all on an exhausted daily budget (429)', async () => {
        const showToast = vi.fn();
        const { result, bodies, calls } = await sendWith(
            baseProps({ showToast, inference: { ...INFERENCE_DEFAULTS, think: 'high' } }),
            () => ({ ok: false, status: 429 }),
        );
        // One request, zero retries — retrying an exhausted budget only
        // re-spends tokens. The interception must fire BEFORE the think and
        // tools chains, both of which are armed here (level + tools).
        expect(calls).toBe(1);
        expect(bodies[0].tools).toEqual(TOOLS);
        expect(showToast).toHaveBeenCalledWith(
            expect.stringContaining('Daily inference budget exhausted'),
            6000,
        );
        // The budget toast is the ONLY toast — the generic "Chat failed"
        // catch must not pile a duplicate on top of it.
        expect(showToast).toHaveBeenCalledTimes(1);
        // The budget state drives the composer's send-disable in ChatView.
        expect(result.current.inferenceBudget).toMatchObject({ remaining_tokens: 0 });
        // No self-heal, no fallback toasts — the failure is budget, not model.
        expect(result.current.messages.map((m) => m.content)).toEqual(['hello', '']);
    });

    it('gives up after both fallbacks without looping', async () => {
        const showToast = vi.fn();
        const { result, calls } = await sendWith(
            baseProps({ showToast, inference: { ...INFERENCE_DEFAULTS, think: 'high' } }),
            () => errorResponse(400),
        );
        // Request 1 (rejected level) + request 2 (downgraded think) +
        // request 3 (tools dropped) — then the 400 stands and the turn
        // fails. No fourth request, no infinite loop.
        expect(calls).toBe(3);
        expect(result.current.isStreaming).toBe(false);
        expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Chat failed'), 5000);
    });
});

describe('useChatEngine refreshModels model-list shapes', () => {
    const jsonResponse = (obj) => ({ ok: true, status: 200, json: async () => obj });

    // The 400ms debounce is real timers; waitFor rides it out.
    const modelsFetch = (payload) => chatFetch.mockImplementation(async () => jsonResponse(payload));

    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(console, 'warn').mockImplementation(() => {});
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('accepts the gateway shape: models as plain name strings', async () => {
        // Regression: the gateway returns ["qwen3.5:latest", ...] — mapping
        // .name over strings yielded undefined and rendered blank options.
        modelsFetch({
            models: ['qwen3.5:latest', 'llama3.2:3b'],
            budget: { remaining_tokens: 500, reset_at: '2026-09-18T00:00:00Z' },
        });
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await waitFor(() => {
            expect(result.current.availableModels).toEqual(['qwen3.5:latest', 'llama3.2:3b']);
        }, { timeout: 3000 });
        // Server mode: the budget rides along with the model list.
        expect(result.current.inferenceBudget).toMatchObject({ remaining_tokens: 500 });
        expect(result.current.reachable).toBe(true);
    });

    it('accepts the local /api/tags shape: objects with .name, no budget', async () => {
        modelsFetch({ models: [{ name: 'qwen3.5:latest' }, { name: 'llama3.2:3b' }] });
        const { result } = renderHook(() => useChatEngine(baseProps({ inferenceSource: 'local' })));
        await waitFor(() => {
            expect(result.current.availableModels).toEqual(['qwen3.5:latest', 'llama3.2:3b']);
        }, { timeout: 3000 });
        // Local mode never carries a budget.
        expect(result.current.inferenceBudget).toBeNull();
        expect(result.current.reachable).toBe(true);
    });
});
