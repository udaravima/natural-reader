import { describe, it, expect } from 'vitest';
import {
    createSnapshotWorkspace, indexDirectoryHandle, pickEntryFile, isNavigable,
} from './workspace';

function fakeFile(relPath, content) {
    const f = new File([content], relPath.split('/').pop(), { type: 'text/markdown' });
    Object.defineProperty(f, 'webkitRelativePath', { value: relPath });
    return f;
}

describe('createSnapshotWorkspace', () => {
    it('strips the root segment and indexes by relative path', async () => {
        const ws = createSnapshotWorkspace([
            fakeFile('vault/README.md', '# Home'),
            fakeFile('vault/sub/b.md', '# B'),
        ]);
        expect(ws.rootName).toBe('vault');
        expect(ws.hasFile('README.md')).toBe(true);
        expect(ws.hasFile('sub/b.md')).toBe(true);
        expect(ws.hasFile('vault/README.md')).toBe(false);
        expect(ws.listFiles().sort()).toEqual(['README.md', 'sub/b.md']);
        expect(await ws.readText('sub/b.md')).toBe('# B');
    });
});

describe('indexDirectoryHandle', () => {
    it('walks a fake handle recursively', async () => {
        const fileHandle = (name) => ({ kind: 'file', name, getFile: async () => new File(['x'], name) });
        const dir = {
            kind: 'directory', name: 'root',
            async *entries() {
                yield ['a.md', fileHandle('a.md')];
                yield ['sub', {
                    kind: 'directory', name: 'sub',
                    async *entries() { yield ['c.md', fileHandle('c.md')]; },
                }];
            },
        };
        const index = await indexDirectoryHandle(dir);
        expect([...index.keys()].sort()).toEqual(['a.md', 'sub/c.md']);
    });
});

describe('pickEntryFile', () => {
    it('prefers README.md, then index.md, then first md alphabetically', () => {
        expect(pickEntryFile(['z.md', 'README.md', 'a.md'])).toBe('README.md');
        expect(pickEntryFile(['z.md', 'index.md', 'a.md'])).toBe('index.md');
        expect(pickEntryFile(['z.md', 'a.md'])).toBe('a.md');
        expect(pickEntryFile(['notes.txt'])).toBe(null);
    });
});

describe('isNavigable', () => {
    it('accepts md/markdown/txt only', () => {
        expect(isNavigable('a.md')).toBe(true);
        expect(isNavigable('a.markdown')).toBe(true);
        expect(isNavigable('a.txt')).toBe(true);
        expect(isNavigable('a.png')).toBe(false);
    });
});
