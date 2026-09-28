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

    it('treats a 404 (session row not yet persisted) as benign: returns false without notifying offline', async () => {
        fetchMock.mockResolvedValue({ ok: false, status: 404, text: async () => '' });
        const onBackendOffline = vi.fn();
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline });
        const ok = await store.updateSessionPins('s-1', []);
        expect(ok).toBe(false);
        expect(onBackendOffline).not.toHaveBeenCalled();
    });
});

vi.mock('../db', () => ({
    getRecentSessions: async () => [],
    deleteSession: async () => true,
    saveSession: async () => true,
    getSession: async () => ({
        id: 's-old', title: 'Old', model: 'qwen2.5', createdAt: 1,
        messages: [{ id: 'u-1', role: 'user', content: 'q', timestamp: 1,
            attachments: [{ id: 'a', kind: 'image', name: 'x.png', mimeType: 'image/png', size: 3, base64: 'AAA', dataUrl: 'data:' }] }],
        events: [], pins: [],
    }),
}));

describe('importLegacy', () => {
    let fetchMock;
    beforeEach(() => {
        fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{"ok":true,"id":"s-new"}' });
        vi.stubGlobal('fetch', fetchMock);
    });
    afterEach(() => vi.unstubAllGlobals());

    it('POSTs the legacy chat without attachment bytes and resolves the new id', async () => {
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline: () => {} });
        expect(await store.importLegacy('s-old')).toBe('s-new');
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toContain('/v1/chat/sessions/import');
        expect(init.method).toBe('POST');
        const sent = JSON.parse(init.body);
        expect(sent.title).toBe('Old');
        expect(sent.messages[0].attachments).toEqual([{ id: 'a', kind: 'image', name: 'x.png', mimeType: 'image/png', size: 3 }]);
    });
});
