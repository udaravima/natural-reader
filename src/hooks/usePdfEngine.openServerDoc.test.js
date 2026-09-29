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
vi.mock('../utils/docHash', () => ({ forgetDocHash: vi.fn() }));

import { saveBook } from '../db';
import { getDocument } from 'pdfjs-dist';
import { forgetDocHash } from '../utils/docHash';
import { apiFetch } from '../utils/apiFetch';
import { fetchDocFile } from '../lib/serverDocFile';
import { saveReadingProgress } from './usePersistedState';

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

        expect(opened).toEqual({ numPages: expect.any(Number) });
        expect(result.current.pdfFileName).toBe(name);
        expect(result.current.fileType).toBe(fileType);
        expect(saveBook).toHaveBeenCalledWith(file, { page: 1, sentenceIndex: -1 });
    });

    it('application/pdf opens through pdf.js and resolves with its page count', async () => {
        const doc = { numPages: 9, getOutline: vi.fn(async () => []) };
        getDocument.mockReturnValue({ promise: Promise.resolve(doc) });
        apiFetch.mockResolvedValue(new Response('%PDF-1.4', { status: 200, headers: { 'Content-Type': 'application/pdf' } }));
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus: vi.fn(), setToastMessage: vi.fn() }));

        const file = await fetchDocFile('', '', 'a'.repeat(64), 'Paper');
        let opened;
        await act(async () => { opened = await result.current.processFile(file); });

        expect(opened).toEqual({ numPages: 9 });
        expect(result.current.pdfFileName).toBe('Paper');
        expect(result.current.fileType).toBe('pdf');
        expect(saveBook).toHaveBeenCalledWith(file, { page: 1, sentenceIndex: -1 });
    });

    it('a PDF pdf.js cannot load resolves null', async () => {
        getDocument.mockReturnValue({ promise: Promise.reject(new Error('bad pdf')) });
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus: vi.fn(), setToastMessage: vi.fn() }));
        let opened;
        await act(async () => { opened = await result.current.processFile(new File(['x'], 'x.pdf', { type: 'application/pdf' })); });
        expect(opened).toBeNull();
    });

    it('new bytes under a name drop any hash cached for that name, once they are saved', async () => {
        let finishSave;
        saveBook.mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve; }));
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus: vi.fn(), setToastMessage: vi.fn() }));
        let opening;
        act(() => { opening = result.current.processFile(new File(['hello'], 'same.txt', { type: 'text/plain' })); });
        await vi.waitFor(() => expect(saveBook).toHaveBeenCalled());
        // A hash taken while the write is in flight reads the old record, so
        // the name is forgotten only after the write lands.
        expect(forgetDocHash).not.toHaveBeenCalled();
        await act(async () => { finishSave(true); await opening; });
        expect(forgetDocHash).toHaveBeenCalledWith('same.txt');
    });

    it('a page set right after opening (a citation) keeps the saved sentence from taking over', async () => {
        // Saved position: page 2, sentence 1. The citation then goes to page 3.
        saveReadingProgress('cited.txt', 2, 1);
        const text = Array.from({ length: 60 }, (_, i) => `Sentence number ${i + 1}.`).join(' ');
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus: vi.fn(), setToastMessage: vi.fn() }));
        await act(async () => { await result.current.processFile(new File([text], 'cited.txt', { type: 'text/plain' })); });
        expect(result.current.currentPage).toBe(2);
        act(() => { result.current.setCurrentPage(3); result.current.setCurrentSentenceIndex(-1); });

        await act(async () => { await new Promise((r) => setTimeout(r, 700)); });
        expect(result.current.currentPage).toBe(3);
        expect(result.current.currentSentenceIndex).toBe(-1);
    });

    it('with no page change after opening, the saved sentence is still restored', async () => {
        saveReadingProgress('resume.txt', 2, 1);
        const text = Array.from({ length: 60 }, (_, i) => `Sentence number ${i + 1}.`).join(' ');
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus: vi.fn(), setToastMessage: vi.fn() }));
        await act(async () => { await result.current.processFile(new File([text], 'resume.txt', { type: 'text/plain' })); });
        await act(async () => { await new Promise((r) => setTimeout(r, 700)); });
        expect(result.current.currentPage).toBe(2);
        expect(result.current.currentSentenceIndex).toBe(1);
    });
});
