import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePdfEngine } from './usePdfEngine';

vi.mock('../db', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        saveBook: vi.fn(async () => true),
        getBook: vi.fn(async () => null),
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
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));

import { saveBook } from '../db';
import { apiFetch } from '../utils/apiFetch';
import { fetchDocFile } from '../lib/serverDocFile';

// Library → Open (App.jsx openServerDoc): fetch the stored bytes as a File,
// then open it through processFile exactly as a picked file — so it is saved
// to the local library under the Library's name and the reader shows it with
// the right type, and processFile's promise says when it's open.
describe('opening a server document', () => {
    beforeEach(() => vi.clearAllMocks());

    it.each([
        ['text/markdown; charset=utf-8', 'Shared notes', 'markdown'],
        ['text/plain; charset=utf-8', 'plain.txt', 'text'],
    ])('%s opens as %s under the Library name', async (type, name, fileType) => {
        apiFetch.mockResolvedValue(new Response('# Title\n\nBody text here.', { status: 200, headers: { 'Content-Type': type } }));
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus: vi.fn(), setToastMessage: vi.fn() }));

        const file = await fetchDocFile('', '', 'a'.repeat(64), name);
        let opened;
        await act(async () => { opened = await result.current.processFile(file); });

        expect(opened).toBe(true);
        expect(result.current.pdfFileName).toBe(name);
        expect(result.current.fileType).toBe(fileType);
        expect(saveBook).toHaveBeenCalledWith(file, { page: 1, sentenceIndex: -1 });
    });
});
