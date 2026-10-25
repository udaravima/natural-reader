import { describe, it, expect, beforeEach } from 'vitest';
import { getOrComputeDocHash, forgetDocHash, clearDocHashCache, sha256Hex, readOpenDocBytes, openDocHash } from './docHash';

const buf = (s) => new TextEncoder().encode(s).buffer;

describe('docHash cache', () => {
    beforeEach(() => clearDocHashCache());

    it('forgetDocHash drops a name, so new same-size bytes under it hash afresh', async () => {
        const a = await getOrComputeDocHash('paper.pdf', buf('AAAA'));
        forgetDocHash('paper.pdf');
        const b = await getOrComputeDocHash('paper.pdf', buf('BBBB'));
        expect(a).not.toBe(b);
        expect(b).toBe(await sha256Hex(buf('BBBB')));
    });

    it('a hash still being computed when its name is forgotten is not cached', async () => {
        const pending = getOrComputeDocHash('paper.pdf', buf('OLD!'));  // load N, in flight
        forgetDocHash('paper.pdf');                                   // load N+1 saved new bytes
        await pending;
        const next = await getOrComputeDocHash('paper.pdf', buf('NEW!')); // same size
        expect(next).toBe(await sha256Hex(buf('NEW!')));
    });
});

describe('the open document', () => {
    beforeEach(() => clearDocHashCache());
    const library = { 'notes.md': { data: buf('library copy') } };
    const getBook = async (name) => library[name];

    it('a library document is read from the library', async () => {
        const src = { fileName: 'notes.md', inLibrary: true, bytes: null, getBook };
        expect(new TextDecoder().decode(await readOpenDocBytes(src))).toBe('library copy');
        expect(await openDocHash(src)).toBe(await sha256Hex(buf('library copy')));
    });

    it('a workspace file is its own bytes, never a same-named library book', async () => {
        const src = { fileName: 'notes.md', inLibrary: false, bytes: buf('folder copy'), getBook };
        expect(new TextDecoder().decode(await readOpenDocBytes(src))).toBe('folder copy');
        expect(await openDocHash(src)).toBe(await sha256Hex(buf('folder copy')));
        // and its hash isn't cached under the name, where the library book would find it
        expect(await openDocHash({ ...src, inLibrary: true, bytes: null })).toBe(await sha256Hex(buf('library copy')));
    });

    it('nothing open, or no bytes: null', async () => {
        expect(await openDocHash({ fileName: '', inLibrary: true, bytes: null, getBook })).toBe(null);
        expect(await openDocHash({ fileName: 'gone.md', inLibrary: true, bytes: null, getBook })).toBe(null);
        expect(await openDocHash({ fileName: 'notes.md', inLibrary: false, bytes: null, getBook })).toBe(null);
    });
});
