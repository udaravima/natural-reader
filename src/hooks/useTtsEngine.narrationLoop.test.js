import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useTtsEngine } from './useTtsEngine';

// The reader's narration loop continues from a sentence's "ended" event. Anything that stops the audio another
// way (Skip, a page turned by hand) or leaves the loop nothing to play (a page with no sentences) used to end
// the narration silently, with the Pause button still showing.
describe('the narration loop keeps going', () => {
    const saved = { speech: window.speechSynthesis, create: URL.createObjectURL, revoke: URL.revokeObjectURL };
    const pausedDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'paused');
    let playing;      // the audio elements that are audibly playing
    let plays;        // how many times play() was called
    let spoken;       // utterances handed to the browser voice
    let failSynth;

    beforeEach(() => {
        playing = new Set();
        plays = 0;
        spoken = [];
        failSynth = false;
        globalThis.SpeechSynthesisUtterance = function Utterance(text) { this.text = text; };
        window.speechSynthesis = { speaking: false, cancel: vi.fn(), speak: vi.fn((u) => spoken.push(u.text)) };
        let n = 0;
        URL.createObjectURL = vi.fn(() => `blob:test/${++n}`);
        URL.revokeObjectURL = vi.fn();
        Object.defineProperty(HTMLMediaElement.prototype, 'paused', { configurable: true, get() { return !playing.has(this); } });
        vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function play() {
            plays += 1;
            playing.add(this);
            return Promise.resolve();
        });
        vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function pause() { playing.delete(this); });
        apiFetch.mockImplementation(async () => {
            if (failSynth) throw new Error('down');
            return { ok: true, json: async () => ({ audio_base64: 'UklGRg==' }) };
        });
    });
    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
        Object.defineProperty(HTMLMediaElement.prototype, 'paused', pausedDesc);
        window.speechSynthesis = saved.speech;
        URL.createObjectURL = saved.create;
        URL.revokeObjectURL = saved.revoke;
    });

    // The parent's state, as App holds it: the sentence index and the page.
    const start = (over = {}) => {
        const state = { index: -1, page: 1 };
        const playbackIndexRef = { current: -1 };
        const setCurrentSentenceIndex = vi.fn((v) => {
            state.index = typeof v === 'function' ? v(state.index) : v;
            playbackIndexRef.current = state.index;      // usePdfEngine's sync effect
        });
        const setCurrentPage = vi.fn((v) => { state.page = typeof v === 'function' ? v(state.page) : v; });
        const setStatus = vi.fn();
        const showToast = vi.fn();
        const props = (more = {}) => ({
            textItems: ['One.', 'Two.', 'Three.'], textItemsPage: 1, currentSentenceIndex: state.index,
            setCurrentSentenceIndex, playbackIndexRef, currentPage: 1, setCurrentPage, numPages: 3,
            selectedVoice: 'v', playbackSpeed: 1, isLocalhost: true, volume: 1, apiHost: '', apiPort: '',
            requestTimeout: 30, unlimitedBatchTimeout: false, backendAvailable: true, pdfFileName: 'a.pdf',
            setStatus, showToast, ...over, ...more,
        });
        const hook = renderHook((p) => useTtsEngine(p), { initialProps: props() });
        return { ...hook, props, state, setCurrentPage, setStatus, showToast, setCurrentSentenceIndex };
    };
    const play = async (h) => {
        act(() => { h.result.current.setIsPlaying(true); });
        await waitFor(() => expect(plays).toBe(1));
        expect(h.state.index).toBe(0);
    };

    it('Skip plays the next sentence', async () => {
        const h = await (async () => { const x = start(); await play(x); return x; })();
        h.rerender(h.props({ currentSentenceIndex: 0 }));
        act(() => { h.result.current.skipToNextSentence(); });
        await waitFor(() => expect(plays).toBe(2));
        expect(h.state.index).toBe(1);
        expect(h.result.current.isPlaying).toBe(true);
    });

    it('Skip on the last sentence of the page changes nothing', async () => {
        const h = start({ textItems: ['Only.'] });
        await play(h);
        h.rerender(h.props({ textItems: ['Only.'], currentSentenceIndex: 0 }));
        act(() => { h.result.current.skipToNextSentence(); });
        await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
        expect(plays).toBe(1);
        expect(playing.size).toBe(1);          // still reading it
    });

    it('a page turned by hand stops the old page and reads the new one from its top', async () => {
        const h = start();
        await play(h);
        // The page changes under a sentence that is still audible; its text is cleared, then arrives.
        h.rerender(h.props({ currentPage: 2, textItems: [], textItemsPage: null, currentSentenceIndex: 0 }));
        expect(playing.size).toBe(0);
        h.rerender(h.props({ currentPage: 2, textItems: ['New one.', 'New two.'], textItemsPage: 2, currentSentenceIndex: -1 }));
        await waitFor(() => expect(plays).toBe(2));
        expect(h.state.index).toBe(0);
        const texts = apiFetch.mock.calls.map(([, , , init]) => JSON.parse(init.body).text);
        expect(texts).toContain('New one.');
    });

    it('a page with nothing to read is passed over', async () => {
        const h = start();
        await play(h);
        h.rerender(h.props({ currentPage: 2, textItems: [], textItemsPage: null, currentSentenceIndex: -1 }));
        expect(h.setCurrentPage).not.toHaveBeenCalled();           // still loading: wait
        h.rerender(h.props({ currentPage: 2, textItems: [], textItemsPage: 2, currentSentenceIndex: -1 }));
        await waitFor(() => expect(h.setCurrentPage).toHaveBeenCalled());
        h.state.page = 2;
        h.setCurrentPage.mock.calls.forEach(([v]) => { if (typeof v === 'function') h.state.page = v(2); });
        expect(h.state.page).toBe(3);
        expect(h.result.current.isPlaying).toBe(true);
    });

    it('paging backwards onto a page with nothing to read keeps going backwards', async () => {
        const h = start({ currentPage: 3 });
        await play(h);
        h.rerender(h.props({ currentPage: 2, textItems: [], textItemsPage: 2, currentSentenceIndex: -1 }));
        await waitFor(() => expect(h.setCurrentPage).toHaveBeenCalled());
        const [update] = h.setCurrentPage.mock.calls.at(-1);
        expect(update(2)).toBe(1);
    });

    it('an empty last page ends the document instead of hanging', async () => {
        const h = start({ numPages: 2 });
        await play(h);
        h.rerender(h.props({ numPages: 2, currentPage: 2, textItems: [], textItemsPage: 2, currentSentenceIndex: -1 }));
        await waitFor(() => expect(h.result.current.isPlaying).toBe(false));
        expect(h.setStatus).toHaveBeenLastCalledWith('End of Document');
    });

    it('the same page handed over again mid-sentence still continues to the next one', async () => {
        const h = start();
        await play(h);
        h.rerender(h.props({ textItems: ['One.', 'Two.', 'Three.'], currentSentenceIndex: 0 }));   // a new array, same text
        const audio = [...playing][0];
        playing.delete(audio);
        act(() => { audio.onended(); });
        await waitFor(() => expect(plays).toBe(2));
        expect(h.state.index).toBe(1);
    });

    it('reads with the browser voice when the server was found unreachable', async () => {
        const h = start({ backendAvailable: false });
        act(() => { h.result.current.setIsPlaying(true); });
        await waitFor(() => expect(spoken).toEqual(['One.']));
        expect(apiFetch).not.toHaveBeenCalled();
    });

    it('goes over to the browser voice when the server stops answering, and says so', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        failSynth = true;
        const h = start();
        act(() => { h.result.current.setIsPlaying(true); });
        await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
        await waitFor(() => expect(spoken).toEqual(['One.']));
        expect(h.result.current.isPlaying).toBe(true);
        expect(h.showToast.mock.calls.some(([m]) => /browser/i.test(m))).toBe(true);
    });
});
