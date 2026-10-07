import { useState, useEffect } from 'react';
import { getLibraryOwner, readingProgressKey } from '../db';

const PREFIX = 'neural-pdf-';

/**
 * A useState variant that persists the value to localStorage.
 * Replaces the 12+ identical useEffect blocks that were in App.jsx.
 */
export function usePersistedState(key, defaultValue) {
    const [value, setValue] = useState(() => {
        try {
            const stored = localStorage.getItem(`${PREFIX}${key}`);
            if (stored !== null) {
                return JSON.parse(stored);
            }
        } catch (e) {
            console.warn(`Failed to load ${key} from localStorage`, e);
        }
        return defaultValue;
    });

    useEffect(() => {
        localStorage.setItem(`${PREFIX}${key}`, JSON.stringify(value));
    }, [key, value]);

    return [value, setValue];
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
