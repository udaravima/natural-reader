import { useEffect } from 'react';
import { SHORTCUTS } from '../constants';

/**
 * Registers global keyboard shortcuts for playback, navigation, and zoom.
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
    useEffect(() => {
        const handleKeyDown = (e) => {
            // Don't trigger shortcuts when typing in inputs
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

            switch (e.key) {
                case SHORTCUTS.PLAY_PAUSE:
                    e.preventDefault();
                    handlePlayPause();
                    break;
                case SHORTCUTS.STOP:
                    stopPlayback();
                    break;
                case SHORTCUTS.PREV_SENTENCE:
                    if (e.shiftKey) {
                        setCurrentSentenceIndex(prev => Math.max(-1, prev - 1));
                    }
                    break;
                case SHORTCUTS.NEXT_SENTENCE:
                    if (e.shiftKey) {
                        skipToNextSentence();
                    }
                    break;
                case SHORTCUTS.PREV_PAGE:
                    setCurrentPage(p => Math.max(1, p - 1));
                    break;
                case SHORTCUTS.NEXT_PAGE:
                    setCurrentPage(p => Math.min(numPages, p + 1));
                    break;
                case SHORTCUTS.ZOOM_IN:
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        setScale(s => Math.min(3, s + 0.2));
                    }
                    break;
                case SHORTCUTS.ZOOM_OUT:
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        setScale(s => Math.max(0.5, s - 0.2));
                    }
                    break;
                case SHORTCUTS.TOGGLE_DARK:
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        setDarkMode(d => !d);
                    }
                    break;
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [numPages]);
}
