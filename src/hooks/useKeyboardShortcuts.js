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
    setDistractionFree,
    numPages,
    viewMode,
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
            setDistractionFree,
            numPages,
            viewMode,
        };
    });

    useEffect(() => {
        const handleKeyDown = (e) => {
            // Don't trigger shortcuts when typing in inputs
            const tag = e.target.tagName;
            if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

            const cb = callbacksRef.current;
            // Reader-only shortcuts (play/skip/page-nav) — dark-mode + zoom still work in chat.
            const inReader = cb.viewMode !== 'chat';

            switch (e.key) {
                case SHORTCUTS.PLAY_PAUSE:
                    if (!inReader) break;
                    e.preventDefault();
                    cb.handlePlayPause();
                    break;
                case SHORTCUTS.STOP:
                    if (!inReader) break;
                    cb.stopPlayback();
                    break;
                case SHORTCUTS.PREV_SENTENCE:
                    if (inReader && e.shiftKey) {
                        cb.setCurrentSentenceIndex(prev => Math.max(-1, prev - 1));
                    }
                    break;
                case SHORTCUTS.NEXT_SENTENCE:
                    if (inReader && e.shiftKey) {
                        cb.skipToNextSentence();
                    }
                    break;
                case SHORTCUTS.PREV_PAGE:
                    if (!inReader) break;
                    cb.setCurrentPage(p => Math.max(1, p - 1));
                    break;
                case SHORTCUTS.NEXT_PAGE:
                    if (!inReader) break;
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
                case SHORTCUTS.TOGGLE_DISTRACTION_FREE:
                    // Bare-key toggle (no modifier) — works in reader and chat
                    // since the chat list also benefits from a minimal frame.
                    if (!e.ctrlKey && !e.metaKey && !e.altKey && cb.setDistractionFree) {
                        e.preventDefault();
                        cb.setDistractionFree(v => !v);
                    }
                    break;
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []); // Stable — uses refs for latest values
}
