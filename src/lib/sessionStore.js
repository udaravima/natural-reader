/**
 * Session-storage abstraction wrapping legacy IndexedDB + new Postgres backend.
 *
 * Reads merge both stores; writes always target Postgres. IDB-stored sessions
 * (created before this feature shipped) appear in the sidebar with source='local'
 * and are read-only — if the user edits one, the first save forks to a new
 * Postgres session (the original IDB row stays untouched).
 *
 * When the backend is unreachable, read calls degrade to IDB-only and write
 * calls resolve falsy without throwing. Callers (useChatEngine) treat that as
 * "session not persisted" and surface a toast — the live chat itself is
 * independent (it streams against Ollama directly).
 */
import * as idb from '../db';
import { buildApiUrl } from '../utils/url';

const LOCAL_IDS = new Set();
let lastBackendReachable = null; // null=unknown, true/false

const newId = () => `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export function makeSessionStore({ apiHost, apiPort, onBackendOffline }) {
    const url = (path) => buildApiUrl(apiHost, apiPort, path);

    const fetchJson = async (path, init) => {
        const res = await fetch(url(path), init);
        if (!res.ok) {
            const err = new Error(`HTTP ${res.status}`);
            err.status = res.status;
            throw err;
        }
        // 204 / empty body
        if (res.status === 204) return null;
        const text = await res.text();
        return text ? JSON.parse(text) : null;
    };

    const notifyOffline = (e) => {
        if (lastBackendReachable !== false) {
            lastBackendReachable = false;
            onBackendOffline?.(e);
        }
    };
    const notifyOnline = () => { lastBackendReachable = true; };

    return {
        /**
         * List sessions from both stores, tagged with `source`. Newest first.
         * Backend failure is silent — the caller still sees legacy IDB sessions.
         */
        getRecentSessions: async () => {
            const [pgRes, idbRes] = await Promise.allSettled([
                fetchJson('/v1/chat/sessions'),
                idb.getRecentSessions(),
            ]);

            const pg = (pgRes.status === 'fulfilled' && Array.isArray(pgRes.value))
                ? pgRes.value.map((s) => ({ ...s, source: 'pg' }))
                : [];
            if (pgRes.status === 'fulfilled') notifyOnline();
            else notifyOffline(pgRes.reason);

            const local = (idbRes.status === 'fulfilled')
                ? idbRes.value.map((s) => ({ ...s, source: 'local' }))
                : [];

            LOCAL_IDS.clear();
            for (const s of local) LOCAL_IDS.add(s.id);

            return [...pg, ...local].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
        },

        /**
         * Load a full session. Looks in IDB if the id is known-local, otherwise
         * tries Postgres first and falls back to IDB on 404 / network error.
         */
        getSession: async (id) => {
            if (!id) return null;
            if (LOCAL_IDS.has(id)) return idb.getSession(id);
            try {
                const data = await fetchJson(`/v1/chat/sessions/${encodeURIComponent(id)}`);
                notifyOnline();
                return data;
            } catch (e) {
                if (e.status !== 404) notifyOffline(e);
                return idb.getSession(id);
            }
        },

        /**
         * Upsert a session record to Postgres. If the id was originally from IDB,
         * mint a new pg id so the legacy record stays put — the returned object
         * has `.id` set to whatever was persisted; the caller updates its
         * activeSessionId accordingly.
         */
        saveSession: async (record) => {
            if (!record?.id) return null;
            let saveId = record.id;
            const forkedFromLocal = LOCAL_IDS.has(record.id);
            if (forkedFromLocal) {
                saveId = newId();
            }
            const payload = { ...record, id: saveId };
            try {
                await fetchJson(`/v1/chat/sessions/${encodeURIComponent(saveId)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                notifyOnline();
                return { id: saveId, forkedFrom: forkedFromLocal ? record.id : null };
            } catch (e) {
                notifyOffline(e);
                return null;
            }
        },

        /**
         * Delete a session. IDB sessions delete locally; Postgres sessions hit
         * the API.
         */
        deleteSession: async (id) => {
            if (!id) return false;
            if (LOCAL_IDS.has(id)) {
                LOCAL_IDS.delete(id);
                return idb.deleteSession(id);
            }
            try {
                await fetchJson(`/v1/chat/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
                notifyOnline();
                return true;
            } catch (e) {
                if (e.status === 404) return true; // already gone
                notifyOffline(e);
                return false;
            }
        },

        /**
         * Rename via the PATCH endpoint for pg sessions; legacy IDB sessions are
         * renamed in place.
         */
        renameSession: async (id, newTitle) => {
            const trimmed = (newTitle || '').trim();
            if (!id || !trimmed) return false;
            if (LOCAL_IDS.has(id)) {
                const record = await idb.getSession(id);
                if (!record) return false;
                record.title = trimmed.slice(0, 60);
                record.updatedAt = Date.now();
                return idb.saveSession(record);
            }
            try {
                await fetchJson(`/v1/chat/sessions/${encodeURIComponent(id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: trimmed.slice(0, 60) }),
                });
                notifyOnline();
                return true;
            } catch (e) {
                notifyOffline(e);
                return false;
            }
        },

        /**
         * Persist just the pins for a session (instant save on pin add/remove).
         * Mirrors renameSession: local IDB sessions update in place; pg sessions
         * hit the PATCH endpoint. Degrades to falsy/offline without throwing.
         */
        updateSessionPins: async (id, pins) => {
            if (!id) return false;
            if (LOCAL_IDS.has(id)) {
                const record = await idb.getSession(id);
                if (!record) return false;
                record.pins = pins;
                record.updatedAt = Date.now();
                return idb.saveSession(record);
            }
            try {
                await fetchJson(`/v1/chat/sessions/${encodeURIComponent(id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pins }),
                });
                notifyOnline();
                return true;
            } catch (e) {
                notifyOffline(e);
                return false;
            }
        },

        /** True if `id` is in the legacy IDB store. Used by callers that want to
         * disable destructive UI for read-only sessions. */
        isLocalId: (id) => LOCAL_IDS.has(id),
    };
}
