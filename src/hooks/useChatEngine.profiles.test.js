import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChatEngine } from './useChatEngine';
import { INFERENCE_DEFAULTS } from './inference';
import { postTurn } from '../lib/chatStream';
import { apiFetch } from '../utils/apiFetch';
import { bodyOf, loadFixture, sseText } from '../lib/chatFixtures.testutil';

// The profile picker (profile library spec, "For a person"): the profiles an admin left on, one picked per chat.
vi.mock('../lib/chatStream', async (importOriginal) => ({ ...(await importOriginal()), postTurn: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
const store = vi.hoisted(() => ({
    getRecentSessions: vi.fn(async () => []),
    getSession: vi.fn(async () => null),
    isLocalId: vi.fn(() => false),
}));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: () => store }));

const MODELS = [{ id: 'ollama:m', provider: 'ollama', kind: 'ollama', name: 'm', capabilities: {} }];
const PLAIN = { id: 'p1', name: 'Plain', description: 'Short answers', isDefault: true };
const TUTOR = { id: 'p2', name: 'Tutor', description: '', isDefault: false };
const REVIEWER = { id: 'p3', name: 'Reviewer', description: 'Reads twice', isDefault: false };
const ok = (events) => ({ ok: true, status: 200, body: bodyOf(sseText(events)), clone() { return this; } });
const turnUsing = (profileId, profileName) => ok(loadFixture('plain').map((e) => (e.type === 'start'
    ? { ...e, profileId, profileName } : e)));
const props = () => ({
    selectedModel: 'ollama:m', chatTtsMode: 'after-complete', chatAutoTts: false, inference: { ...INFERENCE_DEFAULTS },
    isLocalhost: false, selectedVoice: 'af_heart', playbackSpeed: 1, requestTimeout: 15, apiHost: '', apiPort: '8000',
    currentDocId: null, synthesizeText: vi.fn(), playChatUrl: vi.fn(), playChatSpeech: vi.fn(), stopChatPlayback: vi.fn(),
    showToast: vi.fn(),
});
const answer = (body) => ({ ok: true, status: 200, json: async () => body });

let listed;
beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    store.getRecentSessions.mockResolvedValue([]);
    store.isLocalId.mockReturnValue(false);
    listed = [PLAIN, TUTOR];
    apiFetch.mockImplementation(async (_h, _p, path) => {
        if (path === '/v1/chat/profiles') return answer({ profiles: listed });
        if (path === '/v1/settings/helper') return answer({ on: false });
        return answer({ models: MODELS, budget: null });
    });
});
afterEach(() => vi.useRealTimers());

const sent = (i = 0) => postTurn.mock.calls[i][0].body.context.profile_id;
const send = (result, text = 'Hi') => act(async () => { await result.current.sendMessage(text); });

describe('useChatEngine — profiles', () => {
    it('reads the profiles at the start and when the window is looked at again; no answer keeps them', async () => {
        const reads = () => apiFetch.mock.calls.filter((c) => c[2] === '/v1/chat/profiles').length;
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.profiles).toEqual([PLAIN, TUTOR]));
        listed = [PLAIN, TUTOR, REVIEWER];
        await act(async () => { window.dispatchEvent(new Event('focus')); });
        await waitFor(() => expect(result.current.profiles).toHaveLength(3));
        apiFetch.mockImplementation(async () => { throw new Error('offline'); });
        await act(async () => { await result.current.refreshProfiles(); });
        expect(result.current.profiles).toHaveLength(3);
        expect(reads()).toBeGreaterThanOrEqual(2);
    });

    it('a new chat shows the default and sends no pick', async () => {
        postTurn.mockResolvedValue(turnUsing('p1', 'Plain'));
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.profileId).toBe('p1'));
        await send(result);
        expect(sent()).toBeNull();
    });

    it('a pick applies to the next message, then the chat keeps it; new chats start from it, even after a reload', async () => {
        postTurn.mockResolvedValue(turnUsing('p2', 'Tutor'));
        const { result, unmount } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.profiles).toHaveLength(2));
        act(() => { result.current.setProfile('p2'); });
        expect(result.current.profileId).toBe('p2');
        await send(result);
        expect(sent(0)).toBe('p2');
        await send(result, 'And?');
        expect(sent(1)).toBeNull();                                   // the chat has it now: the server knows
        expect(result.current.profileId).toBe('p2');
        unmount();
        const again = renderHook(() => useChatEngine(props())).result;
        await waitFor(() => expect(again.current.profileId).toBe('p2'));
        await send(again);
        expect(sent(2)).toBe('p2');
    });

    it("a remembered pick that is no longer listed falls back to the default", async () => {
        const first = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(first.result.current.profiles).toHaveLength(2));
        act(() => { first.result.current.setProfile('p2'); });
        first.unmount();
        listed = [PLAIN, REVIEWER];
        postTurn.mockResolvedValue(turnUsing('p1', 'Plain'));
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.profiles).toEqual([PLAIN, REVIEWER]));
        expect(result.current.profileId).toBe('p1');
        await send(result);
        expect(sent()).toBeNull();
    });

    it("an opened chat shows its own profile, not the remembered pick, and the server's answer corrects the picker", async () => {
        store.getSession.mockResolvedValue({ id: 's-1', messages: [], pins: [], events: [], profileId: 'p1', profileName: 'Plain' });
        postTurn.mockResolvedValue(turnUsing('p1', 'Plain'));
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.profiles).toHaveLength(2));
        act(() => { result.current.setProfile('p2'); });              // remembered for new chats
        await act(async () => { await result.current.switchToSession('s-1'); });
        expect(result.current.profileId).toBe('p1');
        act(() => { result.current.setProfile('p2'); });
        postTurn.mockResolvedValue(turnUsing('p1', 'Plain'));         // p2 was turned off meanwhile: the chat's own
        await send(result);
        expect(sent()).toBe('p2');
        expect(result.current.profileId).toBe('p1');
    });

    it('says when the profile a chat used was removed, until the next message', async () => {
        store.getSession.mockResolvedValue({ id: 's-1', messages: [], pins: [], events: [], profileId: null,
            profileName: 'Tutor', profileRemoved: true });
        postTurn.mockResolvedValue(turnUsing('p1', 'Plain'));
        const { result } = renderHook(() => useChatEngine(props()));
        await waitFor(() => expect(result.current.profiles).toHaveLength(2));
        await act(async () => { await result.current.switchToSession('s-1'); });
        expect(result.current.removedProfile).toBe('Tutor');
        expect(result.current.profileId).toBe('p1');
        await send(result);
        expect(result.current.removedProfile).toBeNull();
        act(() => { result.current.newSession(); });
        expect(result.current.removedProfile).toBeNull();
    });
});
