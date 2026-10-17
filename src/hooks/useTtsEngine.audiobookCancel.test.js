import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useTtsEngine } from './useTtsEngine';

// The audiobook export reads every page's text before it synthesizes. That
// first phase used to run before the export existed: no progress, no Cancel,
// and a second click could start it again. The export now owns the whole job.
describe('audiobook export cancel', () => {
    beforeEach(() => {
        window.speechSynthesis = { speaking: false, cancel: vi.fn(), speak: vi.fn() };
        apiFetch.mockImplementation(async () => ({ ok: true, json: async () => ({ audio_base64: 'UklGRg==' }) }));
    });
    afterEach(() => vi.restoreAllMocks());

    const props = () => ({
        textItems: [], currentSentenceIndex: -1, setCurrentSentenceIndex: vi.fn(),
        playbackIndexRef: { current: -1 }, currentPage: 1, setCurrentPage: vi.fn(), numPages: 1,
        selectedVoice: 'v', playbackSpeed: 1, isLocalhost: true, volume: 1, apiHost: '', apiPort: '',
        requestTimeout: 30, unlimitedBatchTimeout: false, backendAvailable: true, pdfFileName: 'a.pdf',
        setStatus: vi.fn(), showToast: vi.fn(),
    });

    const slowLoader = () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        const load = vi.fn(async () => { await gate; return [{ page: 1, text: 'One. Two.' }]; });
        return { load, release };
    };

    it('shows progress while it reads the document, before any synthesis', async () => {
        const { result } = renderHook(() => useTtsEngine(props()));
        const { load, release } = slowLoader();
        let done;
        act(() => { done = result.current.downloadBookAudio(load, 'book'); });
        await waitFor(() => expect(result.current.bookProgress?.label).toMatch(/reading the document/i));
        expect(apiFetch).not.toHaveBeenCalled();
        result.current.cancelBookDownload();
        release();
        await act(async () => { await done; });
    });

    it('a Cancel while it reads the document sends no synthesis at all', async () => {
        const { result } = renderHook(() => useTtsEngine(props()));
        const { load, release } = slowLoader();
        let done;
        act(() => { done = result.current.downloadBookAudio(load, 'book'); });
        await waitFor(() => expect(result.current.bookProgress).not.toBeNull());
        act(() => { result.current.cancelBookDownload(); });
        release();
        let ok;
        await act(async () => { ok = await done; });
        expect(ok).toBe(false);
        expect(apiFetch).not.toHaveBeenCalled();
        expect(result.current.bookProgress).toBeNull();
    });

    it('a second click while it reads the document doesn\'t start a second export', async () => {
        const { result } = renderHook(() => useTtsEngine(props()));
        const first = slowLoader();
        const second = slowLoader();
        let done;
        act(() => { done = result.current.downloadBookAudio(first.load, 'book'); });
        await waitFor(() => expect(result.current.bookProgress).not.toBeNull());
        let again;
        await act(async () => { again = await result.current.downloadBookAudio(second.load, 'book'); });
        expect(again).toBe(false);
        expect(second.load).not.toHaveBeenCalled();
        act(() => { result.current.cancelBookDownload(); });
        first.release();
        await act(async () => { await done; });
    });

    it('still accepts a ready list of pages', async () => {
        URL.createObjectURL = vi.fn(() => 'blob:x');
        URL.revokeObjectURL = vi.fn();
        const { result } = renderHook(() => useTtsEngine(props()));
        let ok;
        await act(async () => { ok = await result.current.downloadBookAudio([{ page: 1, text: 'One.' }], 'book'); });
        expect(apiFetch).toHaveBeenCalledTimes(1);
        expect(typeof ok).toBe('boolean');
    });
});
