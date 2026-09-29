import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useTtsEngine } from './useTtsEngine';

// The reader's <audio> element must never hold a revoked blob: URL — the
// next seek or reload of it fetches the dead URL (ERR_FILE_NOT_FOUND, seen in
// the A1 walk on reopening a PDF). A URL is revoked when the element moves
// on to another one, or on unmount — never while it is still loaded.
describe('reader audio blob URLs', () => {
    let n;
    let revoked;
    let audios;

    beforeEach(() => {
        n = 0;
        revoked = [];
        audios = [];
        window.speechSynthesis = { speaking: false, cancel: vi.fn(), speak: vi.fn() };
        URL.createObjectURL = vi.fn(() => `blob:test/${++n}`);
        URL.revokeObjectURL = vi.fn((u) => revoked.push(u));
        vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function play() {
            if (!audios.includes(this)) audios.push(this);
            return Promise.resolve();
        });
        vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
        apiFetch.mockImplementation(async () => ({ ok: true, json: async () => ({ audio_base64: 'UklGRg==' }) }));
    });
    afterEach(() => vi.restoreAllMocks());

    const props = (textItems) => ({
        textItems, currentSentenceIndex: -1, setCurrentSentenceIndex: vi.fn(),
        playbackIndexRef: { current: -1 }, currentPage: 1, setCurrentPage: vi.fn(), numPages: 1,
        selectedVoice: 'v', playbackSpeed: 1, isLocalhost: true, volume: 1, apiHost: '', apiPort: '',
        requestTimeout: 30, unlimitedBatchTimeout: false, backendAvailable: true, pdfFileName: 'a.pdf',
        setStatus: vi.fn(), showToast: vi.fn(), enabled: true,
    });

    it('keeps the loaded URL through its end and a page/document change; revokes it when replaced or on unmount', async () => {
        const first = props(['One.', 'Two.']);
        const { result, rerender, unmount } = renderHook((p) => useTtsEngine(p), { initialProps: first });
        act(() => { result.current.setIsPlaying(true); });

        await waitFor(() => expect(audios.length).toBe(1));
        const audio = audios[0];
        await waitFor(() => expect(audio.src).toMatch(/^blob:test\//));
        const loaded = audio.src;

        // The document is reopened: new textItems clear the cache.
        rerender(props(['New page.']));
        expect(revoked).not.toContain(loaded);

        // The sentence ends: still loaded, so still alive.
        act(() => { audio.onended?.(); });
        expect(revoked).not.toContain(loaded);

        unmount();
        expect(revoked).toContain(loaded);
    });

    it('revokes the previous URL once the element has moved on to the next sentence', async () => {
        const { result } = renderHook((p) => useTtsEngine(p), { initialProps: props(['One.', 'Two.', 'Three.']) });
        act(() => { result.current.setIsPlaying(true); });
        await waitFor(() => expect(audios.length).toBe(1));
        const audio = audios[0];
        await waitFor(() => expect(audio.src).toMatch(/^blob:test\//));
        const firstUrl = audio.src;

        await act(async () => { audio.onended(); });
        await waitFor(() => expect(audio.src).not.toBe(firstUrl));
        expect(revoked).toContain(firstUrl);
        expect(revoked).not.toContain(audio.src);
    });
});
