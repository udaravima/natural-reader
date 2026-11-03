import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useTtsEngine } from './useTtsEngine';

// The chat and Talk speak through /v1/chat/voice, which the server answers with Kokoro or Google (spec
// 2026-11-03-google-speech); documents keep /v1/synthesize.
describe('the chat voice', () => {
    beforeEach(() => {
        URL.createObjectURL = vi.fn(() => 'blob:x');
        URL.revokeObjectURL = vi.fn();
        apiFetch.mockImplementation(async () => ({ ok: true, json: async () => ({ audio_base64: 'UklGRg==' }) }));
    });
    afterEach(() => vi.restoreAllMocks());

    const props = {
        textItems: [], currentSentenceIndex: -1, setCurrentSentenceIndex: vi.fn(), playbackIndexRef: { current: -1 },
        currentPage: 1, setCurrentPage: vi.fn(), numPages: 1, selectedVoice: 'bf_emma', playbackSpeed: 1.2,
        isLocalhost: true, volume: 1, apiHost: '', apiPort: '', requestTimeout: 30, unlimitedBatchTimeout: false,
        backendAvailable: true, pdfFileName: 'a.pdf', setStatus: vi.fn(), showToast: vi.fn(),
    };

    it('asks the chat voice route, with the person\'s own voice and speed for Kokoro to use', async () => {
        const { result } = renderHook(() => useTtsEngine(props));
        expect(await result.current.synthesizeChat('Hello.')).toBe('blob:x');
        const [, , path, request] = apiFetch.mock.calls[0];
        expect(path).toBe('/v1/chat/voice');
        expect(JSON.parse(request.body)).toEqual({ text: 'Hello.', voice: 'bf_emma', speed: 1.2 });
    });

    it('a document sentence still goes to Kokoro', async () => {
        const { result } = renderHook(() => useTtsEngine(props));
        await result.current.synthesizeText('Page one.');
        expect(apiFetch.mock.calls[0][2]).toBe('/v1/synthesize');
    });

    it('a refusal is no audio, as before', async () => {
        apiFetch.mockImplementation(async () => ({ ok: false, status: 503, json: async () => ({}) }));
        vi.spyOn(console, 'error').mockImplementation(() => {});
        const { result } = renderHook(() => useTtsEngine(props));
        expect(await result.current.synthesizeChat('Hello.')).toBeNull();
    });
});
