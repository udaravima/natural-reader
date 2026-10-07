import { useCallback, useMemo, useState } from 'react';
import { docUseKey } from '../db';

const read = (userId) => {
    if (!userId) return {};
    try {
        const stored = JSON.parse(localStorage.getItem(docUseKey(userId)) || '{}');
        return stored && typeof stored === 'object' ? stored : {};
    } catch {
        return {};
    }
};

const write = (userId, sessionId, enabled) => {
    if (!userId || !sessionId) return;
    const all = read(userId);
    if (enabled) delete all[sessionId];
    else all[sessionId] = false;          // only "off" is stored: absent means on
    try {
        localStorage.setItem(docUseKey(userId), JSON.stringify(all));
    } catch {
        // storage full or blocked: the choice still holds for this session
    }
};

/**
 * "Use this document" (v2.4 Task E): whether a chat's turns send the open
 * document. On by default; off is kept per signed-in user and per chat, so
 * the next user on this browser never inherits it (the v2.2 rule). A new
 * chat has no session id until its first message: its choice is held here
 * and `adopt(sessionId)` stores it under the id the chat then gets.
 */
export function useDocUse(userId, sessionId) {
    // The new-chat choice, for the user it was made by.
    const [pending, setPending] = useState({ ownerId: userId, enabled: true });
    const [, setVersion] = useState(0);     // a write re-renders, so `enabled` re-reads storage
    const pendingEnabled = pending.ownerId === userId ? pending.enabled : true;

    const enabledFor = useCallback(
        (sid) => (sid ? read(userId)[sid] !== false : pendingEnabled),
        [userId, pendingEnabled],
    );

    const setEnabled = useCallback((on) => {
        if (!sessionId) {
            setPending({ ownerId: userId, enabled: on });
            return;
        }
        write(userId, sessionId, on);
        setVersion((v) => v + 1);
    }, [userId, sessionId]);

    // `enabled` defaults to the new-chat choice; pass it to carry another
    // chat's choice (a browser-only chat forked to the server gets a new id).
    const adopt = useCallback((sid, enabled = pendingEnabled) => {
        if (!enabled) write(userId, sid, false);
        setPending({ ownerId: userId, enabled: true });
        setVersion((v) => v + 1);
    }, [userId, pendingEnabled]);

    const enabled = enabledFor(sessionId);
    return useMemo(() => ({ enabled, setEnabled, enabledFor, adopt }), [enabled, setEnabled, enabledFor, adopt]);
}
