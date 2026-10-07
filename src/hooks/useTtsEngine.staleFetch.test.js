import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useTtsEngine } from './useTtsEngine';

// v2.2 Task B: a clip still being synthesized when the page changes (a page
// turn, a new document, a citation jump) must not land in the new page's
// cache at the same index — the new page would play the old page's sentence.
describe('read-aloud cache across a page change', () => {
    let n;
    let revoked;
    let audios;
    let held; // text → resolve() for synthesis requests the test controls
    const saved = { speech: window.speechSynthesis, create: URL.createObjectURL, revoke: URL.revokeObjectURL };

    beforeEach(() => {
        n = 0;
        revoked = [];
        audios = [];
        held = new Map();
        window.speechSynthesis = { speaking: false, cancel: vi.fn(), speak: vi.fn() };
        URL.createObjectURL = vi.fn(() => `blob:test/${++n}`);
        URL.revokeObjectURL = vi.fn((u) => revoked.push(u));
        vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function play() {
            if (!audios.includes(this)) audios.push(this);
            return Promise.resolve();
        });
        vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
        apiFetch.mockImplementation(async (host, port, path, init) => {
            const { text } = JSON.parse(init.body);
            const ok = { ok: true, json: async () => ({ audio_base64: 'UklGRg==' }) };
            if (text === 'Old two.') return new Promise((resolve) => held.set(text, () => resolve(ok)));
            return ok;
        });
    });
    afterEach(() => {
        vi.restoreAllMocks();
        window.speechSynthesis = saved.speech;
        URL.createObjectURL = saved.create;
        URL.revokeObjectURL = saved.revoke;
    });

    const props = (textItems) => ({
        textItems, currentSentenceIndex: -1, setCurrentSentenceIndex: vi.fn(),
        playbackIndexRef: { current: -1 }, currentPage: 1, setCurrentPage: vi.fn(), numPages: 2,
        selectedVoice: 'v', playbackSpeed: 1, isLocalhost: true, volume: 1, apiHost: '', apiPort: '',
        requestTimeout: 30, unlimitedBatchTimeout: false, backendAvailable: true, pdfFileName: 'a.pdf',
        setStatus: vi.fn(), showToast: vi.fn(), enabled: true,
    });

    it('a clip that finishes after the page changed is discarded, not cached for the new page', async () => {
        const { result, rerender } = renderHook((p) => useTtsEngine(p), { initialProps: props(['Old one.', 'Old two.']) });
        act(() => { result.current.setIsPlaying(true); });
        await waitFor(() => expect(held.has('Old two.')).toBe(true)); // index 1 prefetch in flight

        // The page changes: new text, the cache is cleared.
        rerender(props(['New one.', 'New two.']));
        const sent = (text) => apiFetch.mock.calls.some(([, , , init]) => JSON.parse(init.body).text === text);
        await waitFor(() => expect(sent('New two.')).toBe(true)); // the new page's own prefetch
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        const urlsBefore = n;
        await act(async () => { held.get('Old two.')(); });
        await waitFor(() => expect(n).toBe(urlsBefore + 1));
        const staleUrl = `blob:test/${n}`;
        expect(revoked).toContain(staleUrl);

        // Index 1 on the new page plays its own clip, never the stale one.
        const audio = audios[0];
        await act(async () => { audio.onended?.(); });
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        expect(audio.src).toMatch(/^blob:test\//);
        expect(audio.src).not.toBe(staleUrl);
    });
});
