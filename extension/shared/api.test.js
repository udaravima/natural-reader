import { describe, it, expect, vi } from 'vitest';
import { health, synthesize } from './api.js';

const BASE = 'http://localhost:8000';

describe('health', () => {
    it('returns true when status ok and model loaded', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ status: 'ok', model_loaded: true }),
        });
        expect(await health({ baseUrl: BASE, fetchImpl })).toBe(true);
        expect(fetchImpl).toHaveBeenCalledWith(`${BASE}/v1/health`);
    });
    it('returns false on non-ok or model not loaded', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ status: 'error', model_loaded: false }),
        });
        expect(await health({ baseUrl: BASE, fetchImpl })).toBe(false);
    });
    it('returns false when fetch throws', async () => {
        const fetchImpl = vi.fn().mockRejectedValue(new Error('connection refused'));
        expect(await health({ baseUrl: BASE, fetchImpl })).toBe(false);
    });
});

describe('synthesize', () => {
    it('posts the batch payload and decodes base64 to a WAV Blob', async () => {
        const b64 = btoa('RIFFfake'); // 8 bytes
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ audio_base64: b64, duration_seconds: 1 }),
        });
        const blob = await synthesize({
            sentences: ['Hi.', 'There.'], voice: 'af_heart', speed: 1.0, baseUrl: BASE, fetchImpl,
        });
        expect(blob).toBeInstanceOf(Blob);
        expect(blob.type).toBe('audio/wav');
        expect(blob.size).toBe(8);
        const [url, opts] = fetchImpl.mock.calls[0];
        expect(url).toBe(`${BASE}/v1/batch_synthesize`);
        expect(opts.method).toBe('POST');
        expect(JSON.parse(opts.body)).toEqual({
            sentences: ['Hi.', 'There.'], voice: 'af_heart', speed: 1.0,
        });
    });
    it('throws with the server detail on HTTP error', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: false, status: 500, json: async () => ({ detail: 'boom' }),
        });
        await expect(synthesize({
            sentences: ['x'], voice: 'af_heart', speed: 1, baseUrl: BASE, fetchImpl,
        })).rejects.toThrow(/boom/);
    });
});
