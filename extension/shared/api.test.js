import { describe, it, expect, vi } from 'vitest';
import { health, synthesize, synthesizeBase64, checkConnection, base64ToBlob } from './api.js';

const BASE = 'http://localhost:8000';
const TOKEN = 'nrp_test';

function res(status, body) {
    return { ok: status >= 200 && status < 300, status, json: async () => body };
}

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

describe('synthesizeBase64 (the service-worker path)', () => {
    it('sends the access token as a Bearer header and returns the base64 audio', async () => {
        const fetchImpl = vi.fn().mockResolvedValue(res(200, { audio_base64: 'QUJD' }));
        const b64 = await synthesizeBase64({
            sentences: ['Hi.'], voice: 'af_heart', speed: 1, baseUrl: BASE, token: TOKEN, fetchImpl,
        });
        expect(b64).toBe('QUJD');
        expect(fetchImpl.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`);
    });
    it('sends no Authorization header without a token (single-user backend)', async () => {
        const fetchImpl = vi.fn().mockResolvedValue(res(200, { audio_base64: 'QUJD' }));
        await synthesizeBase64({ sentences: ['Hi.'], voice: 'af_heart', speed: 1, baseUrl: BASE, fetchImpl });
        expect('Authorization' in fetchImpl.mock.calls[0][1].headers).toBe(false);
    });
    it('says how to fix a 401 instead of "Authentication required"', async () => {
        const fetchImpl = vi.fn().mockResolvedValue(res(401, { detail: 'Authentication required' }));
        await expect(synthesizeBase64({
            sentences: ['x'], voice: 'af_heart', speed: 1, baseUrl: BASE, fetchImpl,
        })).rejects.toThrow(/access token/);
    });
    it('names the missing capability on a 403 instead of [object Object]', async () => {
        const fetchImpl = vi.fn().mockResolvedValue(
            res(403, { detail: { error: 'missing_capability', capability: 'reader' } }));
        const p = synthesizeBase64({
            sentences: ['x'], voice: 'af_heart', speed: 1, baseUrl: BASE, token: TOKEN, fetchImpl,
        });
        await expect(p).rejects.toThrow(/reader/);
        await expect(p).rejects.not.toThrow(/object Object/);
    });
});

describe('base64ToBlob', () => {
    it('decodes to a WAV blob of the right size', () => {
        const blob = base64ToBlob(btoa('RIFFfake'));
        expect(blob.type).toBe('audio/wav');
        expect(blob.size).toBe(8);
    });
});

describe('checkConnection', () => {
    function routes(map) {
        return vi.fn(async (url) => {
            const path = new URL(url).pathname;
            if (map[path] instanceof Error) throw map[path];
            return map[path];
        });
    }
    const healthy = res(200, { status: 'ok', model_loaded: true });

    it('is ok, with the account email, when the token is accepted', async () => {
        const fetchImpl = routes({
            '/v1/health': healthy,
            '/v1/auth/me': res(200, { email: 'a@example.com', capabilities: ['reader'] }),
        });
        expect(await checkConnection({ baseUrl: BASE, token: TOKEN, fetchImpl }))
            .toEqual({ ok: true, reason: 'ok', email: 'a@example.com' });
        const meCall = fetchImpl.mock.calls.find(([u]) => u.endsWith('/v1/auth/me'));
        expect(meCall[1].headers.Authorization).toBe(`Bearer ${TOKEN}`);
    });
    it('reports unreachable when the server does not answer', async () => {
        const fetchImpl = routes({ '/v1/health': new Error('refused') });
        expect((await checkConnection({ baseUrl: BASE, token: TOKEN, fetchImpl })).reason)
            .toBe('unreachable');
    });
    it('reports no_token when the server needs sign-in and none is set', async () => {
        const fetchImpl = routes({ '/v1/health': healthy, '/v1/auth/me': res(401, {}) });
        expect((await checkConnection({ baseUrl: BASE, token: '', fetchImpl })).reason)
            .toBe('no_token');
    });
    it('reports bad_token when the server rejects the token', async () => {
        const fetchImpl = routes({ '/v1/health': healthy, '/v1/auth/me': res(401, {}) });
        expect((await checkConnection({ baseUrl: BASE, token: TOKEN, fetchImpl })).reason)
            .toBe('bad_token');
    });
    it('reports no_reader when the account lacks the reader capability', async () => {
        const fetchImpl = routes({
            '/v1/health': healthy,
            '/v1/auth/me': res(200, { email: 'a@example.com', capabilities: ['chat'] }),
        });
        expect((await checkConnection({ baseUrl: BASE, token: TOKEN, fetchImpl })).reason)
            .toBe('no_reader');
    });
    it('reports a disabled account (403 on /me)', async () => {
        const fetchImpl = routes({
            '/v1/health': healthy,
            '/v1/auth/me': res(403, { detail: { status: 'suspended' } }),
        });
        expect((await checkConnection({ baseUrl: BASE, token: TOKEN, fetchImpl })).reason)
            .toBe('account_inactive');
    });
});
