import { useCallback, useEffect, useState } from 'react';
import { getLibraryOwner, readingProgressKey } from '../db';
import { useLatest } from './useLatest';

const PREFIX = 'neural-pdf-';
const RESET_EVENT = 'neural-pdf-reset';

function readStored(key) {
    try {
        const stored = localStorage.getItem(`${PREFIX}${key}`);
        return stored === null ? undefined : JSON.parse(stored);
    } catch (e) {
        console.warn(`Failed to load ${key} from localStorage`, e);
        return undefined;
    }
}

/**
 * A useState variant that persists the user's CHOICE to localStorage.
 *
 * Only a value set through the setter is stored; until then the hook shows
 * `defaultValue`, and follows it when it changes (an admin's deployment
 * default arriving after load, say). It used to store its default on first
 * load as if chosen, so a changed default never reached a browser that had
 * opened the app once. A choice equal to the default is still a choice.
 */
export function usePersistedState(key, defaultValue) {
    const [stored, setStored] = useState(() => readStored(key));
    const value = stored === undefined ? defaultValue : stored;
    const defaultRef = useLatest(defaultValue);

    useEffect(() => {
        if (stored !== undefined) localStorage.setItem(`${PREFIX}${key}`, JSON.stringify(stored));
    }, [key, stored]);

    // resetPersisted(key): drop the choice in every hook showing this key.
    useEffect(() => {
        const onReset = (e) => { if (e.detail === key) setStored(undefined); };
        window.addEventListener(RESET_EVENT, onReset);
        return () => window.removeEventListener(RESET_EVENT, onReset);
    }, [key]);

    const setValue = useCallback((next) => {
        setStored((prev) => {
            const current = prev === undefined ? defaultRef.current : prev;
            return typeof next === 'function' ? next(current) : next;
        });
    }, [defaultRef]);

    return [value, setValue];
}

/** Forget the user's choice for `key`: it follows the default again. */
export function resetPersisted(key) {
    try {
        localStorage.removeItem(`${PREFIX}${key}`);
    } catch (e) {
        console.warn(`Failed to reset ${key} in localStorage`, e);
    }
    window.dispatchEvent(new CustomEvent(RESET_EVENT, { detail: key }));
}

/** Run a one-time browser migration `id` (before the hooks read). */
export function runOnce(id, fn) {
    const k = `${PREFIX}migrations`;
    let done = [];
    try { done = JSON.parse(localStorage.getItem(k) || '[]'); } catch { done = []; }
    if (done.includes(id)) return;
    fn();
    try { localStorage.setItem(k, JSON.stringify([...done, id])); } catch { /* storage full or blocked */ }
}

/**
 * One-time migration of a persisted value: rewrite `from` → `to` in place.
 * Call at the top of a component body (before the matching usePersistedState
 * hook reads the key on first render). Values are compared by JSON identity,
 * so it only rewrites what was actually stored — a deliberate value is kept.
 */
export function migratePersisted(key, from, to) {
    try {
        const stored = localStorage.getItem(`${PREFIX}${key}`);
        if (stored === JSON.stringify(from)) {
            localStorage.setItem(`${PREFIX}${key}`, JSON.stringify(to));
        }
    } catch (e) {
        console.warn(`Failed to migrate ${key} in localStorage`, e);
    }
}

/**
 * Save/load reading progress per PDF file.
 */
// Reading positions belong to the signed-in user (see src/db.js): none are
// saved or restored while no user is known.
export function saveReadingProgress(fileName, currentPage, currentSentenceIndex) {
    const owner = getLibraryOwner();
    if (owner && fileName && currentPage > 0) {
        const progress = {
            page: currentPage,
            sentenceIndex: currentSentenceIndex,
            timestamp: Date.now(),
        };
        localStorage.setItem(readingProgressKey(owner, fileName), JSON.stringify(progress));
    }
}

export function loadReadingProgress(fileName) {
    const owner = getLibraryOwner();
    if (!owner) return null;
    try {
        const stored = localStorage.getItem(readingProgressKey(owner, fileName));
        if (stored) {
            const progress = JSON.parse(stored);
            // Only restore if less than 7 days old
            if (Date.now() - progress.timestamp < 7 * 24 * 60 * 60 * 1000) {
                return progress;
            }
        }
    } catch (e) {
        console.warn('Failed to load reading progress', e);
    }
    return null;
}
