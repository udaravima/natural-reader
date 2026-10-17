import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useTtsEngine } from './useTtsEngine';

// The narration plays in every view now (it used to stop on leaving the
// reader), so the chat's read-aloud and the narration can meet. One voice at
// a time: a reply read aloud pauses the narration, and says so.
describe('one voice at a time', () => {
    let playing;
    beforeEach(() => {
        playing = new Set();
        window.speechSynthesis = { speaking: false, cancel: vi.fn(), speak: vi.fn() };
        URL.createObjectURL = vi.fn(() => 'blob:x');
        URL.revokeObjectURL = vi.fn();
        vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function play() {
            playing.add(this);
            return Promise.resolve();
        });
        vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function pause() {
            playing.delete(this);
        });
        apiFetch.mockImplementation(async () => ({ ok: true, json: async () => ({ audio_base64: 'UklGRg==' }) }));
    });
    afterEach(() => vi.restoreAllMocks());

    const props = (over = {}) => ({
        textItems: ['One.', 'Two.', 'Three.'], currentSentenceIndex: -1, setCurrentSentenceIndex: vi.fn(),
        playbackIndexRef: { current: -1 }, currentPage: 1, setCurrentPage: vi.fn(), numPages: 1,
        selectedVoice: 'v', playbackSpeed: 1, isLocalhost: true, volume: 1, apiHost: '', apiPort: '',
        requestTimeout: 30, unlimitedBatchTimeout: false, backendAvailable: true, pdfFileName: 'a.pdf',
        setStatus: vi.fn(), showToast: vi.fn(), ...over,
    });

    it('a reply read aloud pauses the narration, and Read resumes it', async () => {
        const setStatus = vi.fn();
        const { result, unmount } = renderHook(() => useTtsEngine(props({ setStatus })));
        act(() => { result.current.handlePlayPause(); });
        await waitFor(() => expect(playing.size).toBe(1));
        expect(result.current.narrationHeld).toBe(true);

        act(() => { result.current.playChatUrl('blob:reply'); });
        expect(result.current.isPlaying).toBe(false);
        expect(result.current.pausedForChat).toBe(true);
        expect(result.current.narrationHeld).toBe(true);
        expect(setStatus).toHaveBeenLastCalledWith('Paused for the chat reply');

        act(() => { result.current.handlePlayPause(); });
        expect(result.current.isPlaying).toBe(true);
        expect(result.current.pausedForChat).toBe(false);
        unmount();   // ends the resumed loop before the mocks are restored
    });

    it('a reply read with the narration off leaves it off', () => {
        const { result } = renderHook(() => useTtsEngine(props()));
        act(() => { result.current.playChatUrl('blob:reply'); });
        expect(result.current.pausedForChat).toBe(false);
        expect(result.current.narrationHeld).toBe(false);
    });

    it('Pause keeps the narration held, Stop and a quiet pause let it go', async () => {
        const { result, unmount } = renderHook(() => useTtsEngine(props()));
        act(() => { result.current.handlePlayPause(); });
        act(() => { result.current.handlePlayPause(); });
        expect(result.current.isPlaying).toBe(false);
        expect(result.current.narrationHeld).toBe(true);
        act(() => { result.current.stopPlayback(); });
        expect(result.current.narrationHeld).toBe(false);

        act(() => { result.current.handlePlayPause(); });
        act(() => { result.current.pauseNarration(); });
        expect(result.current.isPlaying).toBe(false);
        expect(result.current.narrationHeld).toBe(false);
        unmount();
    });
});
