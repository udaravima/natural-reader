import { useCallback, useState } from 'react';
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

    const setDraft = useCallback((next) => {
        if (!userId) return;
        setTyped((prev) => {
            const current = prev?.ownerId === userId ? prev.text : load(userId);
            const text = typeof next === 'function' ? next(current) : next;
            try {
                localStorage.setItem(chatDraftKey(userId), JSON.stringify(text));
            } catch {
                // storage full or blocked: the draft still lives for this session
            }
            return { ownerId: userId, text };
        });
    }, [userId]);

    return [draft, setDraft];
}
