import { describe, it, expect, vi, afterEach } from 'vitest';
import { DEFAULT_IDENTITY, footLine, loadAppIdentity } from './appIdentity';

afterEach(() => { vi.unstubAllGlobals(); document.title = ''; });

describe('the app identity', () => {
    it('reads the name from the server and names the tab', async () => {
        const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ name: 'Acme Docs', tagline: 'a library', copyright: '© 2026 Example Org' }) }));
        vi.stubGlobal('fetch', fetchMock);
        const got = await loadAppIdentity('', '8000');
        expect(fetchMock.mock.calls[0][0]).toBe('/v1/app');
        expect(got).toEqual({ name: 'Acme Docs', tagline: 'a library', copyright: '© 2026 Example Org' });
        expect(document.title).toBe('Acme Docs');
    });

    it('keeps the built-in name when the server cannot be reached or answers oddly', async () => {
        vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('down'); }));
        expect(await loadAppIdentity('', '8000')).toEqual(DEFAULT_IDENTITY);
        expect(document.title).toBe('Natural Reader');
        vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ name: 7 }) })));
        expect(await loadAppIdentity('', '8000')).toEqual(DEFAULT_IDENTITY);
        vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })));
        expect(await loadAppIdentity('', '8000')).toEqual(DEFAULT_IDENTITY);
    });

    it('writes the foot line with the version, and the copyright only when there is one', () => {
        expect(footLine(DEFAULT_IDENTITY, '2.12.0')).toBe('Natural Reader 2.12.0');
        expect(footLine({ ...DEFAULT_IDENTITY, name: 'Acme Docs', copyright: '© 2026 Example Org' }, '2.12.0'))
            .toBe('Acme Docs 2.12.0 · © 2026 Example Org');
    });
});
