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

// A menu (the chat's scope chip) uses Space to pick and Escape to close, like a dialog.
describe('useKeyboardShortcuts inside a menu', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    const press = (target, key) => target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    const mount = () => {
        const cb = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(),
            setCurrentSentenceIndex: vi.fn(), setCurrentPage: vi.fn(), setScale: vi.fn(), setDarkMode: vi.fn(),
            setDistractionFree: vi.fn(), numPages: 3, viewMode: 'reader' };
        renderHook(() => useKeyboardShortcuts(cb));
        return cb;
    };

    it('Space and Escape inside a menu leave the narration alone, and Space keeps its default', () => {
        const cb = mount();
        document.body.innerHTML = '<div role="menu"><button role="menuitem" id="i">x</button></div>';
        const item = document.getElementById('i');
        expect(press(item, ' ')).toBe(true);        // not preventDefault()ed: the button still clicks
        press(item, 'Escape');
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('a plain toolbar button still plays and pauses on Space', () => {
        const cb = mount();
        document.body.innerHTML = '<div role="toolbar"><button id="b">x</button></div>';
        press(document.getElementById('b'), ' ');
        expect(cb.handlePlayPause).toHaveBeenCalledTimes(1);
    });
});

// A control that keeps its own keys opts out with data-own-keys (the chat's web offer card):
// the marked element and everything inside it are skipped, and nothing else is.
describe('useKeyboardShortcuts inside a data-own-keys region', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    const press = (target, key) => target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    const mount = () => {
        const cb = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(),
            setCurrentSentenceIndex: vi.fn(), setCurrentPage: vi.fn(), setScale: vi.fn(), setDarkMode: vi.fn(),
            setDistractionFree: vi.fn(), numPages: 3, viewMode: 'reader' };
        renderHook(() => useKeyboardShortcuts(cb));
        return cb;
    };

    it('Space and Escape inside the region leave the narration alone, and Space keeps its default', () => {
        const cb = mount();
        document.body.innerHTML = '<div data-own-keys><p><button id="b">x</button></p></div>';
        const b = document.getElementById('b');
        expect(press(b, ' ')).toBe(true);
        press(b, 'Escape');
        expect(cb.handlePlayPause).not.toHaveBeenCalled();
        expect(cb.stopPlayback).not.toHaveBeenCalled();
    });

    it('a sibling of the region is not exempt', () => {
        const cb = mount();
        document.body.innerHTML = '<div data-own-keys></div><button id="b">x</button>';
        press(document.getElementById('b'), ' ');
        expect(cb.handlePlayPause).toHaveBeenCalledTimes(1);
    });
});
