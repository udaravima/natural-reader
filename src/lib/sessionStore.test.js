import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { makeSessionStore } from './sessionStore';

describe('updateSessionPins', () => {
    let fetchMock;
    beforeEach(() => {
        fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{"ok":true}' });
        vi.stubGlobal('fetch', fetchMock);
    });
    afterEach(() => vi.unstubAllGlobals());

    it('PATCHes the session with a pins body and resolves true', async () => {
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline: () => {} });
        const pins = [{ id: 'p1', doc_id: 'd', fileName: 'f', page: 1, kind: 'selection', text: 't' }];
        const ok = await store.updateSessionPins('s-1', pins);
        expect(ok).toBe(true);
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toContain('/v1/chat/sessions/s-1');
        expect(init.method).toBe('PATCH');
        expect(JSON.parse(init.body)).toEqual({ pins });
    });

    it('returns false when the id is missing', async () => {
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline: () => {} });
        expect(await store.updateSessionPins('', [])).toBe(false);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});
