import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { fetchDocFile } from './serverDocFile';
import { detectFileType } from '../db';

const DOC = 'a'.repeat(64);
const ok = (body, type) => new Response(body, { status: 200, headers: { 'Content-Type': type } });
const refused = (status, detail) =>
    new Response(JSON.stringify({ detail }), { status, headers: { 'Content-Type': 'application/json' } });

describe('fetchDocFile', () => {
    beforeEach(() => vi.clearAllMocks());

    it('returns a File with the Library name, the bytes, and a type the reader detects', async () => {
        apiFetch.mockResolvedValue(ok('# Notes', 'text/markdown; charset=utf-8'));
        const file = await fetchDocFile('h', 'p', DOC, 'notes');
        expect(apiFetch).toHaveBeenCalledWith('h', 'p', `/v1/docs/${DOC}/file`);
        expect(file.name).toBe('notes');
        expect(file.type).toBe('text/markdown');
        expect(await file.text()).toBe('# Notes');
        expect(detectFileType(file)).toBe('markdown'); // no extension needed
    });

    it.each([
        ['application/pdf', 'pdf'],
        ['text/plain; charset=utf-8', 'text'],
        ['application/octet-stream', 'pdf'],
    ])('%s opens as %s', async (type, expected) => {
        apiFetch.mockResolvedValue(ok('x', type));
        expect(detectFileType(await fetchDocFile('', '', DOC, 'f'))).toBe(expected);
    });

    it.each([
        ['application/pdf', 'notes.md', 'notes.md.pdf', 'pdf'],
        ['application/pdf', 'readme.txt', 'readme.txt.pdf', 'pdf'],
        ['text/markdown; charset=utf-8', 'paper.pdf', 'paper.pdf', 'markdown'],
        ['text/plain; charset=utf-8', 'x.md', 'x.md.txt', 'text'],
        ['text/plain; charset=utf-8', 'x.TXT', 'x.TXT', 'text'],
    ])('a %s named %s is renamed %s so it opens as %s', async (type, name, expectedName, kind) => {
        apiFetch.mockResolvedValue(ok('x', type));
        const file = await fetchDocFile('', '', DOC, name);
        expect(file.name).toBe(expectedName);
        expect(detectFileType(file)).toBe(kind);
    });

    it('a 404 throws the no-access notice', async () => {
        apiFetch.mockResolvedValue(refused(404, { error: 'not_found', message: 'Document not found' }));
        await expect(fetchDocFile('', '', DOC, 'f')).rejects.toThrow("doesn't exist or you don't have access");
    });

    it('a 409 bytes_missing throws the server message', async () => {
        apiFetch.mockResolvedValue(refused(409, { error: 'bytes_missing', message: 'Upload the file again first.' }));
        await expect(fetchDocFile('', '', DOC, 'f')).rejects.toThrow('Upload the file again first.');
    });
});
