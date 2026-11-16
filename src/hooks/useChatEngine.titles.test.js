import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChatEngine, TITLE_POLL_MS } from './useChatEngine';
import { INFERENCE_DEFAULTS } from './inference';
import { apiFetch } from '../utils/apiFetch';

// Chat titles (helper spec §6): a new title shows without a reload, and Generate title asks for one.
vi.mock('../lib/chatStream', async (importOriginal) => ({ ...(await importOriginal()), postTurn: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
const store = vi.hoisted(() => ({
    getRecentSessions: vi.fn(async () => []),
    getSession: vi.fn(async () => null),
    generateTitle: vi.fn(async () => ({ ok: true })),
    isLocalId: vi.fn(() => false),
}));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: () => store }));

const props = (over = {}) => ({
    selectedModel: 'ollama:m', chatTtsMode: 'after-complete', chatAutoTts: false,
    inference: { ...INFERENCE_DEFAULTS }, isLocalhost: false, selectedVoice: 'af_heart', playbackSpeed: 1,
    requestTimeout: 15, apiHost: '', apiPort: '8000', currentDocId: null, synthesizeText: vi.fn(), playChatUrl: vi.fn(),
    playChatSpeech: vi.fn(), stopChatPlayback: vi.fn(), showToast: vi.fn(), ...over,
});
const row = (over = {}) => ({ id: 's-1', title: 'How much for hardware?', titleSource: 'cut', titlePending: false,
    source: 'pg', updatedAt: 1, ...over });

beforeEach(() => {
    vi.resetAllMocks();
    store.getRecentSessions.mockResolvedValue([]);
    store.isLocalId.mockReturnValue(false);
    apiFetch.mockImplementation(async (_h, _p, path) => (path === '/v1/settings/helper'
        ? { ok: true, status: 200, json: async () => ({ on: true }) }
        : { ok: true, status: 200, json: async () => ({ models: [], budget: null }) }));
});
afterEach(() => vi.useRealTimers());

describe('useChatEngine — chat titles', () => {
    it('reads whether the helper is on once', async () => {
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.helperOn).toBe(true));
        expect(apiFetch.mock.calls.filter((c) => c[2] === '/v1/settings/helper')).toHaveLength(1);
    });

    it('re-reads the sessions every 4 s while a row says titlePending, and stops when none does', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        store.getRecentSessions
            .mockResolvedValueOnce([row({ titlePending: true })])
            .mockResolvedValueOnce([row({ titlePending: true })])
            .mockResolvedValue([row({ title: 'Hardware budget', titleSource: 'model' })]);
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.sessions[0]?.titlePending).toBe(true));
        const reads = store.getRecentSessions.mock.calls.length;
        expect(TITLE_POLL_MS).toBe(4000);
        await act(async () => { await vi.advanceTimersByTimeAsync(TITLE_POLL_MS); });
        await act(async () => { await vi.advanceTimersByTimeAsync(TITLE_POLL_MS); });
        await waitFor(() => expect(result.current.sessions[0]?.title).toBe('Hardware budget'));
        const settled = store.getRecentSessions.mock.calls.length;
        expect(settled).toBe(reads + 2);
        await act(async () => { await vi.advanceTimersByTimeAsync(TITLE_POLL_MS * 3); });
        expect(store.getRecentSessions.mock.calls.length).toBe(settled);
    });

    it("asks for a title, re-reads the list, and shows the server's refusal", async () => {
        const showToast = vi.fn();
        const { result } = renderHook(() => useChatEngine(props({ showToast })));
        await waitFor(() => expect(result.current.helperOn).toBe(true));
        store.generateTitle.mockResolvedValueOnce({ ok: true });
        const before = store.getRecentSessions.mock.calls.length;
        await act(async () => { expect(await result.current.generateTitle('s-1')).toBe(true); });
        expect(store.generateTitle).toHaveBeenCalledWith('s-1');
        expect(store.getRecentSessions.mock.calls.length).toBe(before + 1);
        store.generateTitle.mockResolvedValueOnce({ ok: false, message: 'Daily inference budget exhausted.' });
        await act(async () => { expect(await result.current.generateTitle('s-1')).toBe(false); });
        expect(showToast).toHaveBeenCalledWith('Daily inference budget exhausted.', 5000);
    });
});
