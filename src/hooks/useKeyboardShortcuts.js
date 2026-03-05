import { useEffect, useRef } from 'react';
import { SHORTCUTS } from '../constants';

/**
 * Registers global keyboard shortcuts for playback, navigation, and zoom.
 * Uses refs to avoid stale closures — the event listener is registered once
 * and always calls the latest callback versions.
 */
export function useKeyboardShortcuts({
    handlePlayPause,
    stopPlayback,
    skipToNextSentence,
    setCurrentSentenceIndex,
    setCurrentPage,
    setScale,
    setDarkMode,
    numPages,
}) {
    // Keep latest callbacks in refs to avoid stale closures
    const callbacksRef = useRef({});
    useEffect(() => {
        callbacksRef.current = {
            handlePlayPause,
            stopPlayback,
            skipToNextSentence,
            setCurrentSentenceIndex,
            setCurrentPage,
            setScale,
            setDarkMode,
            numPages,
        };
    });

    useEffect(() => {
        const handleKeyDown = (e) => {
            // Don't trigger shortcuts when typing in inputs
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

            const cb = callbacksRef.current;

            switch (e.key) {
                case SHORTCUTS.PLAY_PAUSE:
                    e.preventDefault();
                    cb.handlePlayPause();
                    break;
                case SHORTCUTS.STOP:
                    cb.stopPlayback();
                    break;
                case SHORTCUTS.PREV_SENTENCE:
                    if (e.shiftKey) {
                        cb.setCurrentSentenceIndex(prev => Math.max(-1, prev - 1));
                    }
                    break;
                case SHORTCUTS.NEXT_SENTENCE:
                    if (e.shiftKey) {
                        cb.skipToNextSentence();
                    }
                    break;
                case SHORTCUTS.PREV_PAGE:
                    cb.setCurrentPage(p => Math.max(1, p - 1));
                    break;
                case SHORTCUTS.NEXT_PAGE:
                    cb.setCurrentPage(p => Math.min(cb.numPages, p + 1));
                    break;
                case SHORTCUTS.ZOOM_IN:
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        cb.setScale(s => Math.min(3, s + 0.2));
                    }
                    break;
                case SHORTCUTS.ZOOM_OUT:
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        cb.setScale(s => Math.max(0.5, s - 0.2));
                    }
                    break;
                case SHORTCUTS.TOGGLE_DARK:
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        cb.setDarkMode(d => !d);
                    }
                    break;
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []); // Stable — uses refs for latest values
}
