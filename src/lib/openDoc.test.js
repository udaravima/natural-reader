import { describe, it, expect, vi } from 'vitest';
import { openServerDoc, openCitation } from './openDoc';

const DOC = 'a'.repeat(64);

const deps = (over = {}) => ({
    fetchDocFile: vi.fn(async (id, name) => new File(['x'], name)),
    processFile: vi.fn(async () => ({ numPages: 10 })),
    showToast: vi.fn(),
    showReader: vi.fn(),
    goToPage: vi.fn(),
    ...over,
});

describe('openServerDoc', () => {
    it('fetches, opens, then shows the reader', async () => {
        const d = deps();
        expect(await openServerDoc(d, DOC, 'T.pdf')).toEqual({ numPages: 10 });
        expect(d.fetchDocFile).toHaveBeenCalledWith(DOC, 'T.pdf');
        expect(d.processFile).toHaveBeenCalledWith(expect.objectContaining({ name: 'T.pdf' }));
        expect(d.showReader).toHaveBeenCalled();
        expect(d.showToast).not.toHaveBeenCalled();
    });

    it('a refusal toasts and leaves the view alone', async () => {
        const d = deps({ fetchDocFile: vi.fn(async () => { throw new Error("This document doesn't exist or you don't have access."); }) });
        expect(await openServerDoc(d, DOC, 'T.pdf')).toBeNull();
        expect(d.showToast).toHaveBeenCalledWith(
            `Could not open "T.pdf": This document doesn't exist or you don't have access.`, 5000);
        expect(d.processFile).not.toHaveBeenCalled();
        expect(d.showReader).not.toHaveBeenCalled();
    });

    it('a file the reader cannot load toasts and leaves the view alone', async () => {
        const d = deps({ processFile: vi.fn(async () => null) });
        expect(await openServerDoc(d, DOC, 'T.pdf')).toBeNull();
        expect(d.showToast).toHaveBeenCalledWith(`Could not open "T.pdf": the reader couldn't load this file.`, 5000);
        expect(d.showReader).not.toHaveBeenCalled();
    });
});

describe('openCitation', () => {
    it('the cited document already open: show the reader at that page, no fetch', async () => {
        const d = deps();
        await openCitation(d, { docId: DOC, page: 4, docName: 'T.pdf', openDocId: DOC, numPages: 12 });
        expect(d.fetchDocFile).not.toHaveBeenCalled();
        expect(d.showReader).toHaveBeenCalled();
        expect(d.goToPage).toHaveBeenCalledWith(4);
    });

    it('not open: opens it through the Library route, then goes to the page', async () => {
        const d = deps();
        await openCitation(d, { docId: DOC, page: 4, docName: 'T.pdf', openDocId: 'b'.repeat(64), numPages: 3 });
        expect(d.fetchDocFile).toHaveBeenCalledWith(DOC, 'T.pdf');
        expect(d.goToPage).toHaveBeenCalledWith(4);
        // The page is set after the document opened, so it isn't overwritten
        // by the document's saved reading position.
        expect(d.goToPage.mock.invocationCallOrder[0]).toBeGreaterThan(d.processFile.mock.invocationCallOrder[0]);
    });

    it('a page past the end goes to the last page; below 1 goes to 1', async () => {
        const d = deps();
        await openCitation(d, { docId: DOC, page: 40, docName: 'T.pdf', openDocId: null, numPages: 0 });
        expect(d.goToPage).toHaveBeenLastCalledWith(10);
        await openCitation(d, { docId: DOC, page: 0, docName: 'T.pdf', openDocId: DOC, numPages: 12 });
        expect(d.goToPage).toHaveBeenLastCalledWith(1);
    });

    it('a 404 (document no longer readable): a notice, no reader, no navigation', async () => {
        const d = deps({ fetchDocFile: vi.fn(async () => { throw new Error("This document doesn't exist or you don't have access."); }) });
        await openCitation(d, { docId: DOC, page: 4, docName: 'T.pdf', openDocId: null, numPages: 0 });
        expect(d.showToast).toHaveBeenCalledTimes(1);
        expect(d.showReader).not.toHaveBeenCalled();
        expect(d.goToPage).not.toHaveBeenCalled();
    });

    it('a 409 (no stored bytes): the server message as a notice, no navigation', async () => {
        const d = deps({ fetchDocFile: vi.fn(async () => { throw new Error('Upload the file again first.'); }) });
        await openCitation(d, { docId: DOC, page: 4, docName: 'T.pdf', openDocId: null, numPages: 0 });
        expect(d.showToast).toHaveBeenCalledWith('Could not open "T.pdf": Upload the file again first.', 5000);
        expect(d.goToPage).not.toHaveBeenCalled();
    });

    it('a citation with no saved name still opens, under a placeholder name', async () => {
        const d = deps();
        await openCitation(d, { docId: DOC, page: 2, docName: undefined, openDocId: null, numPages: 0 });
        expect(d.fetchDocFile).toHaveBeenCalledWith(DOC, 'Cited document');
    });
});
