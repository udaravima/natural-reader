import { describe, it, expect } from 'vitest';
import { filesFromInput, filesFromDataTransfer } from './folderDrop';

// A fake FileSystemEntry tree. Chrome's readEntries returns at most 100 entries a call, then [] when done.
function fileEntry(path) {
  const name = path.split('/').pop();
  return { isFile: true, isDirectory: false, name, fullPath: `/${path}`, file: (ok) => ok(new File(['x'], name)) };
}
function dirEntry(path, children, perCall = 100) {
  return {
    isFile: false, isDirectory: true, name: path.split('/').pop(), fullPath: `/${path}`,
    createReader: () => {
      let at = 0;
      return { readEntries: (ok) => { const batch = children.slice(at, at + perCall); at += perCall; ok(batch); } };
    },
  };
}
const dropOf = (entries, files = []) => ({ items: entries.map((e) => ({ webkitGetAsEntry: () => e })), files });

describe('folderDrop', () => {
  it('walks a folder whose directory returns 100 entries per readEntries call', async () => {
    const children = Array.from({ length: 250 }, (_, i) => fileEntry(`docs/f${i}.md`));
    const out = await filesFromDataTransfer(dropOf([dirEntry('docs', children)]));
    expect(out).toHaveLength(250);
    expect(out[0].path).toBe('docs/f0.md');
  });

  it('walks subfolders and keeps each path', async () => {
    const tree = dirEntry('h', [fileEntry('h/a.md'), dirEntry('h/b', [fileEntry('h/b/README.md')])]);
    const out = await filesFromDataTransfer(dropOf([tree, fileEntry('top.pdf')]));
    expect(out.map((f) => f.path)).toEqual(['h/a.md', 'h/b/README.md', 'top.pdf']);
  });

  it('does not walk into .git or node_modules', async () => {
    let opened = 0;
    const git = dirEntry('r/.git', [fileEntry('r/.git/HEAD')]);
    const nm = dirEntry('r/node_modules', [fileEntry('r/node_modules/x.md')]);
    for (const d of [git, nm]) {
      const make = d.createReader;
      d.createReader = () => { opened += 1; return make(); };
    }
    const out = await filesFromDataTransfer(dropOf([dirEntry('r', [git, nm, fileEntry('r/a.md')])]));
    expect(out.map((f) => f.path)).toEqual(['r/a.md']);
    expect(opened).toBe(0);
  });

  it('falls back to dataTransfer.files when entries are missing', async () => {
    const f = new File(['x'], 'a.md');
    const out = await filesFromDataTransfer({ items: [{}], files: [f] });
    expect(out).toEqual([{ file: f, path: 'a.md' }]);
  });

  it('takes the folder picker path from webkitRelativePath', () => {
    const f = new File(['x'], 'a.md');
    Object.defineProperty(f, 'webkitRelativePath', { value: 'handbook/a.md' });
    const g = new File(['y'], 'b.md');
    expect(filesFromInput([f, g])).toEqual([{ file: f, path: 'handbook/a.md' }, { file: g, path: 'b.md' }]);
  });
});
