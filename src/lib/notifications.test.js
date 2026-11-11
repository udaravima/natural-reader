import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { fetchUnread, fetchInbox, markRead, markAllRead } from './notifications';

const ok = (body) => ({ ok: true, json: async () => body });

describe('notifications client', () => {
    beforeEach(() => vi.clearAllMocks());

    it('reads the unread counts', async () => {
        apiFetch.mockResolvedValue(ok({ count: 3, action: 1 }));
        expect(await fetchUnread('h', 'p')).toEqual({ count: 3, action: 1 });
        expect(apiFetch).toHaveBeenCalledWith('h', 'p', '/v1/notifications/unread');
    });

    it('reads a page of the inbox', async () => {
        apiFetch.mockResolvedValue(ok({ rows: [], total: 0 }));
        expect(await fetchInbox('', '', { offset: 30, limit: 30 })).toEqual({ rows: [], total: 0 });
        expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/notifications?offset=30&limit=30');
    });

    it('marks ids read', async () => {
        apiFetch.mockResolvedValue(ok({ changed: 2 }));
        expect(await markRead('', '', [1, 2])).toBe(true);
        const [, , path, opts] = apiFetch.mock.calls[0];
        expect(path).toBe('/v1/notifications/read');
        expect(opts.method).toBe('POST');
        expect(JSON.parse(opts.body)).toEqual({ ids: [1, 2] });
        expect(opts.headers['Content-Type']).toBe('application/json');
    });

    it('sends a group of more than 1000 ids in batches the server accepts (at most 1000 each)', async () => {
        apiFetch.mockResolvedValue(ok({ changed: 1000 }));
        const ids = Array.from({ length: 2500 }, (_, i) => i + 1);
        expect(await markRead('', '', ids)).toBe(true);
        const sent = apiFetch.mock.calls.map(([, , , opts]) => JSON.parse(opts.body).ids);
        expect(sent.map((b) => b.length)).toEqual([1000, 1000, 500]);
        expect(sent.flat()).toEqual(ids);
    });

    it('marks everything read', async () => {
        apiFetch.mockResolvedValue(ok({ changed: 5 }));
        expect(await markAllRead('', '')).toBe(true);
        expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/notifications/read-all', { method: 'POST' });
    });

    it('never throws: a refusal or a network error is null or false', async () => {
        apiFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
        expect(await fetchUnread('', '')).toBeNull();
        expect(await fetchInbox('', '', {})).toBeNull();
        expect(await markRead('', '', [1])).toBe(false);
        apiFetch.mockRejectedValue(new Error('offline'));
        expect(await fetchUnread('', '')).toBeNull();
        expect(await markAllRead('', '')).toBe(false);
    });
});
