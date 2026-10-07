import { useCallback, useRef, useState } from 'react';
import { chatDraftKey } from '../db';

const load = (userId) => {
    if (!userId) return '';
    try {
        const stored = localStorage.getItem(chatDraftKey(userId));
        return stored === null ? '' : JSON.parse(stored);
    } catch {
        return '';
    }
};

/**
 * The chat composer's unsent text, kept per signed-in user (it survives a
 * reload, like the old `chatDraft` setting, but the next user on this
 * browser never sees it). No user known: no draft, and nothing is saved.
 * The setter takes a value or an updater, like useState's.
 */
export function useUserDraft(userId) {
    // { ownerId, text }: the draft typed this session, for the user it was
    // typed by. Any other user's draft is read from storage instead.
    const [typed, setTyped] = useState(null);
    const draft = userId ? (typed?.ownerId === userId ? typed.text : load(userId)) : '';

    // The latest typed draft, so the setter can resolve an updater without a
    // side effect inside setState's (pure) updater.
    const typedRef = useRef(null);
    const setDraft = useCallback((next) => {
        if (!userId) return;
        const prev = typedRef.current;
        const current = prev?.ownerId === userId ? prev.text : load(userId);
        const text = typeof next === 'function' ? next(current) : next;
        typedRef.current = { ownerId: userId, text };
        try {
            localStorage.setItem(chatDraftKey(userId), JSON.stringify(text));
        } catch {
            // storage full or blocked: the draft still lives for this session
        }
        setTyped(typedRef.current);
    }, [userId]);

    return [draft, setDraft];
}
