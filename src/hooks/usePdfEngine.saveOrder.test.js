import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { usePdfEngine } from './usePdfEngine';

// db is mocked so `saveBook` can be held open on demand — a real IndexedDB
// write settles too fast (and too unpredictably under fake-indexeddb) to
// reliably observe "not yet resolved" from a test.
vi.mock('../db', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        saveBook: vi.fn(),
        getBook: vi.fn(async () => null),
        getRecentBooks: vi.fn(async () => []),
        updateBookMeta: vi.fn(async () => {}),
        deleteBook: vi.fn(async () => {}),
    };
});

// PDF loading goes through pdfjs-dist's getDocument(); stub it so the PDF
// path resolves without needing a real PDF byte stream.
vi.mock('pdfjs-dist', () => ({
    GlobalWorkerOptions: {},
    getDocument: vi.fn(),
    Util: { transform: vi.fn() },
}));

import { saveBook } from '../db';
import { getDocument } from 'pdfjs-dist';

const setup = () => {
    const setStatus = vi.fn();
    const setToastMessage = vi.fn();
    return { setToastMessage, ...renderHook(() => usePdfEngine({ scale: 1, setStatus, setToastMessage })) };
};

// A held-open promise whose resolution the test controls, so we can observe
// hook state at the instant *after* saveBook was called but *before* it
// settled.
const heldSave = () => {
    let resolve;
    const promise = new Promise((res) => { resolve = res; });
    saveBook.mockImplementation(() => promise);
    return (value) => act(async () => { resolve(value); });
};

beforeEach(() => {
    vi.clearAllMocks();
});

describe('usePdfEngine — pdfFileName does not flip before saveBook resolves', () => {
    it('PDF path: pdfFileName stays empty until saveBook resolves', async () => {
        const resolveSave = heldSave();
        getDocument.mockReturnValue({
            promise: Promise.resolve({ numPages: 3, getOutline: vi.fn(async () => []) }),
        });

        const { result } = setup();
        const file = new File([new Uint8Array([1, 2, 3])], 'doc.pdf', { type: 'application/pdf' });

        act(() => { result.current.processFile(file); });

        await waitFor(() => expect(saveBook).toHaveBeenCalledTimes(1));
        expect(result.current.pdfFileName).toBe('');

        await resolveSave(true);

        await waitFor(() => expect(result.current.pdfFileName).toBe('doc.pdf'));
    });

    it('text path: pdfFileName stays empty until saveBook resolves', async () => {
        const resolveSave = heldSave();
        const { result } = setup();
        const file = new File(['plain text content.'], 'notes.txt', { type: 'text/plain' });

        act(() => { result.current.processFile(file); });

        await waitFor(() => expect(saveBook).toHaveBeenCalledTimes(1));
        expect(result.current.pdfFileName).toBe('');

        await resolveSave(true);

        await waitFor(() => expect(result.current.pdfFileName).toBe('notes.txt'));
    });

    it('markdown path: pdfFileName stays empty until saveBook resolves', async () => {
        const resolveSave = heldSave();
        const { result } = setup();
        const file = new File(['# Heading\n\nBody.'], 'notes.md', { type: 'text/markdown' });

        act(() => { result.current.processFile(file); });

        await waitFor(() => expect(saveBook).toHaveBeenCalledTimes(1));
        expect(result.current.pdfFileName).toBe('');

        await resolveSave(true);

        await waitFor(() => expect(result.current.pdfFileName).toBe('notes.md'));
    });

    it('PDF path: saveBook failure still opens the document but shows the recovery toast', async () => {
        saveBook.mockResolvedValue(false);
        getDocument.mockReturnValue({
            promise: Promise.resolve({ numPages: 1, getOutline: vi.fn(async () => []) }),
        });

        const { result, setToastMessage } = setup();
        const file = new File([new Uint8Array([1])], 'doc2.pdf', { type: 'application/pdf' });

        act(() => { result.current.processFile(file); });

        await waitFor(() => expect(result.current.pdfFileName).toBe('doc2.pdf'));
        expect(setToastMessage).toHaveBeenCalledWith(
            "Couldn't save this file locally — Index and chat about it may not work until you reopen it."
        );
    });

    it('markdown path: saveBook failure still opens the document but shows the recovery toast', async () => {
        saveBook.mockResolvedValue(false);

        const { result, setToastMessage } = setup();
        const file = new File(['# Heading'], 'notes2.md', { type: 'text/markdown' });

        act(() => { result.current.processFile(file); });

        await waitFor(() => expect(result.current.pdfFileName).toBe('notes2.md'));
        expect(setToastMessage).toHaveBeenCalledWith(
            "Couldn't save this file locally — Index and chat about it may not work until you reopen it."
        );
    });
});
