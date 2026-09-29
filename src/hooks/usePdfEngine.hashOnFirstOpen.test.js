import { useEffect, useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { usePdfEngine } from './usePdfEngine';

// db is mocked (not fake-indexeddb) so the test can control exactly when
// `saveBook` lands, the same way `App.jsx`'s "document changed" effect would
// see it land — this is what makes the race deterministic instead of
// depending on real IndexedDB/microtask timing, which was tried first and
// turned out to pass on *both* the buggy and fixed code (too many awaits
// between "file opened" and "read it back" let the real write always win).
vi.mock('../db', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        saveBook: vi.fn(),
        getBook: vi.fn(),
        getRecentBooks: vi.fn(async () => []),
        updateBookMeta: vi.fn(async () => {}),
        deleteBook: vi.fn(async () => {}),
    };
});
vi.mock('pdfjs-dist', () => ({
    GlobalWorkerOptions: {},
    getDocument: vi.fn(),
    Util: { transform: vi.fn() },
}));

import { saveBook, getBook } from '../db';

beforeEach(() => {
    vi.clearAllMocks();
});

// Ruling R3's narrower substitute for a full App-level render test (App.jsx
// pulls in auth, workspace context, and the chat engine — too much to mount
// cheaply here): a hook harness that pairs usePdfEngine with an effect
// shaped exactly like App.jsx:666's INDEXING effect — keyed on `pdfFileName`,
// calling `getBook(pdfFileName)` the instant it changes. If the fix holds,
// that call finds the bytes on the very first render where pdfFileName is
// set; before the fix, `pdfFileName` (and therefore this effect) fires while
// the save is still in flight.
function useEngineWithHashEffect(props) {
    const engine = usePdfEngine(props);
    const [hashEffectFoundBytes, setHashEffectFoundBytes] = useState(null);
    useEffect(() => {
        if (!engine.pdfFileName) return;
        getBook(engine.pdfFileName).then((record) => setHashEffectFoundBytes(!!record));
    }, [engine.pdfFileName]);
    return { ...engine, hashEffectFoundBytes };
}

const setup = () => {
    const setStatus = vi.fn();
    const setToastMessage = vi.fn();
    return renderHook(() => useEngineWithHashEffect({ scale: 1, setStatus, setToastMessage }));
};

describe('the pdfFileName-keyed hash effect finds the bytes on first open', () => {
    it('markdown: getBook(pdfFileName) already resolves truthy the moment pdfFileName is set', async () => {
        let resolveSave;
        let saved = false;
        saveBook.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
        getBook.mockImplementation(async (fileName) => (saved ? { fileName, data: new Uint8Array([1]) } : null));

        const { result } = setup();
        const file = new File(['# Title\n\nBody.'], 'note.md', { type: 'text/markdown' });

        act(() => { result.current.processFile(file); });

        // The save is still in flight — pdfFileName (and thus the hash
        // effect) must not have fired yet.
        await waitFor(() => expect(saveBook).toHaveBeenCalledTimes(1));
        expect(result.current.pdfFileName).toBe('');
        expect(getBook).not.toHaveBeenCalled();

        // Let the write land, then flip pdfFileName.
        saved = true;
        await act(async () => { resolveSave(true); });

        await waitFor(() => expect(result.current.pdfFileName).toBe('note.md'));
        await waitFor(() => expect(result.current.hashEffectFoundBytes).toBe(true));
    });

    it('text: getBook(pdfFileName) already resolves truthy the moment pdfFileName is set', async () => {
        let resolveSave;
        let saved = false;
        saveBook.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
        getBook.mockImplementation(async (fileName) => (saved ? { fileName, data: new Uint8Array([1]) } : null));

        const { result } = setup();
        const file = new File(['hello there.'], 'note.txt', { type: 'text/plain' });

        act(() => { result.current.processFile(file); });

        await waitFor(() => expect(saveBook).toHaveBeenCalledTimes(1));
        expect(result.current.pdfFileName).toBe('');
        expect(getBook).not.toHaveBeenCalled();

        saved = true;
        await act(async () => { resolveSave(true); });

        await waitFor(() => expect(result.current.pdfFileName).toBe('note.txt'));
        await waitFor(() => expect(result.current.hashEffectFoundBytes).toBe(true));
    });
});
