import { apiFetch } from '../utils/apiFetch';

/**
 * The inbox behind the header's bell (/v1/notifications; spec 2026-11-06). Every call answers null or false on a
 * refusal or a network error and never throws: the bell then keeps what it showed last.
 */

async function getJson(host, port, path) {
    try {
        const res = await apiFetch(host, port, path);
        return res.ok ? await res.json() : null;
    } catch {
        return null;
    }
}

async function post(host, port, path, opts = { method: 'POST' }) {
    try {
        const res = await apiFetch(host, port, path, opts);
        return res.ok;
    } catch {
        return false;
    }
}

/** `{count, action}`: unread rows as the list shows them (a group counts once), and how many need action. */
export const fetchUnread = (host, port) => getJson(host, port, '/v1/notifications/unread');

/** `{rows, total}`: a page of rows (groups and single notices) in inbox order. */
export const fetchInbox = (host, port, { offset = 0, limit = 30 } = {}) =>
    getJson(host, port, `/v1/notifications?offset=${offset}&limit=${limit}`);

/** Mark notices read: one, or every member of a group. */
export const markRead = (host, port, ids) => post(host, port, '/v1/notifications/read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
});

export const markAllRead = (host, port) => post(host, port, '/v1/notifications/read-all');
