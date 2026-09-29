import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
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
    Util: { transform: vi.fn(() => [1, 0, 0, 1, 0, 0]) },
}));

import { getDocument } from 'pdfjs-dist';

class RenderingCancelledException extends Error {
    constructor() { super('Rendering cancelled'); this.name = 'RenderingCancelledException'; }
}

// A pdf.js page whose render() hands back a task the test controls, like
// pdf.js's RenderTask: `promise` settles when drawing ends, and `cancel()`
// rejects it with RenderingCancelledException.
function makeDoc() {
    const tasks = [];
    const page = {
        getViewport: () => ({ width: 10, height: 10, transform: [1, 0, 0, 1, 0, 0] }),
        getTextContent: vi.fn(async () => ({ items: [{ str: 'Hello there.', transform: [1, 0, 0, 1, 0, 0] }] })),
        render: vi.fn(() => {
            let settle;
            const promise = new Promise((resolve, reject) => { settle = { resolve, reject }; });
            const task = {
                promise,
                cancel: vi.fn(() => settle.reject(new RenderingCancelledException())),
                finish: () => settle.resolve(),
            };
            tasks.push(task);
            return task;
        }),
    };
    return { doc: { numPages: 3, getPage: vi.fn(async () => page), getOutline: vi.fn(async () => []) }, page, tasks };
}

describe('PDF page rendering', () => {
    beforeEach(() => vi.clearAllMocks());

    it('a second render cancels the first on the same canvas, and the cancel is not an error', async () => {
        const { doc, page, tasks } = makeDoc();
        getDocument.mockReturnValue({ promise: Promise.resolve(doc) });
        const setStatus = vi.fn();
        const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
        const { result } = renderHook(() => usePdfEngine({ scale: 1, setStatus, setToastMessage: vi.fn() }));

        const canvas = document.createElement('canvas');
        canvas.getContext = () => ({});
        act(() => { result.current.canvasRef(canvas); });
        await act(async () => { await result.current.processFile(new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })); });
        await waitFor(() => expect(tasks).toHaveLength(1));

        act(() => { result.current.setCurrentPage(2); });
        await waitFor(() => expect(tasks).toHaveLength(2));

        expect(tasks[0].cancel).toHaveBeenCalled();
        expect(tasks[1].cancel).not.toHaveBeenCalled();
        await act(async () => { tasks[1].finish(); });

        await waitFor(() => expect(setStatus).toHaveBeenCalledWith('Page 2 Ready'));
        expect(setStatus).not.toHaveBeenCalledWith('Render Error');
        expect(errors).not.toHaveBeenCalled();
        // Only the render that finished paints the text layer.
        expect(page.getTextContent.mock.calls.length).toBeGreaterThan(0);
        errors.mockRestore();
    });
});
