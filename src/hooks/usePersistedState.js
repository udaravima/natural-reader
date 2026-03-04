import { useState, useEffect } from 'react';

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
 * Save/load reading progress per PDF file.
 */
export function saveReadingProgress(fileName, currentPage, currentSentenceIndex) {
    if (fileName && currentPage > 0) {
        const progress = {
            page: currentPage,
            sentenceIndex: currentSentenceIndex,
            timestamp: Date.now(),
        };
        localStorage.setItem(`${PREFIX}progress-${fileName}`, JSON.stringify(progress));
    }
}

export function loadReadingProgress(fileName) {
    try {
        const stored = localStorage.getItem(`${PREFIX}progress-${fileName}`);
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
