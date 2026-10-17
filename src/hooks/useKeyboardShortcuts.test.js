import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useKeyboardShortcuts } from './useKeyboardShortcuts';

// Settings is a dialog over the reader now, so the reader's keys are live
// underneath it: Space must press the dialog's button, not toggle the
// narration, and Escape must close the dialog, not stop the narration.
describe('useKeyboardShortcuts inside a dialog', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    const mount = () => {
        const cb = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(),
            setCurrentSentenceIndex: vi.fn(), setCurrentPage: vi.fn(), setScale: vi.fn(), setDarkMode: vi.fn(),
            setDistractionFree: vi.fn(), numPages: 3, viewMode: 'reader' };
        renderHook(() => useKeyboardShortcuts(cb));
        return cb;
    };
    const press = (target, key) => target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));

    it('Space and Escape inside a dialog leave the narration alone', () => {
        const cb = mount();
        document.body.innerHTML = '<div role="dialog"><button id="b">x</button></div>';
        const b = document.getElementById('b');
        press(b, ' ');
        press(b, 'Escape');
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('outside a dialog, Space still plays and pauses', () => {
        const cb = mount();
        document.body.innerHTML = '<button id="b">x</button>';
        press(document.getElementById('b'), ' ');
        expect(cb.handlePlayPause).toHaveBeenCalledTimes(1);
    });
});
