import { describe, it, expect } from 'vitest';
import {
  isHiddenPath, nameKey, collect, markRepeats, choicesFor, defaultChoice, requestFor, summarize,
} from './uploadBatch';

const PDF_HEAD = new Uint8Array([37, 80, 68, 70, 45]); // %PDF-
const item = (path, data = 'x') => ({ file: new File([data], path.split('/').pop()), path });
const readHead = async (file) => new Uint8Array(await file.slice(0, 5).arrayBuffer());
const doc = (over = {}) => ({ id: 'd1', name: 'setup.pdf', version: 2, ...over });
const row = (over = {}) => ({ path: 'a', name: 'a.pdf', size: 10, action: 'new', document: null, version: null,
  candidates: [], ...over });

describe('collect', () => {
  it('leaves out any path with a dot part or node_modules', () => {
    expect(isHiddenPath('repo/.git/objects/ab')).toBe(true);
    expect(isHiddenPath('a/node_modules/x/README.md')).toBe(true);
    expect(isHiddenPath('a/b/.env')).toBe(true);
    expect(isHiddenPath('.hidden.md')).toBe(true);
    expect(isHiddenPath('a/b/notes.md')).toBe(false);
    expect(isHiddenPath('a/b.c/notes.md')).toBe(false);
  });

  it('drops hidden paths without counting them', async () => {
    const out = await collect([item('r/.git/HEAD'), item('r/node_modules/a.md'), item('r/a.md')], { readHead });
    expect(out.files.map((f) => f.path)).toEqual(['r/a.md']);
    expect(out.leftOut).toBe(0);
  });

  it('gathers never-accepted types into a count and keeps an extensionless PDF', async () => {
    const out = await collect([
      item('a/pic.png'), item('a/notes.docx'), item('a/scan', PDF_HEAD), item('a/README', 'just text'),
      item('a/x.PDF'), item('a/y.markdown'), item('a/z.txt'), item('a/w.md'),
    ], { readHead });
    expect(out.files.map((f) => f.name)).toEqual(['scan', 'w.md', 'x.PDF', 'y.markdown', 'z.txt']);
    expect(out.leftOut).toBe(3);
  });

  it('sorts by path the same way every time', async () => {
    const out = await collect([item('b/README.md'), item('x10.md'), item('a/README.md'), item('x2.md')], { readHead });
    expect(out.files.map((f) => f.path)).toEqual(['a/README.md', 'b/README.md', 'x2.md', 'x10.md']);
  });
});

describe('names', () => {
  it('compares names the way the server does', () => {
    expect(nameKey(' Setup.PDF ')).toBe('setup.pdf');
    expect(nameKey('a/b/Setup.pdf')).toBe('setup.pdf');
  });

  it('marks the later of two names that differ only by case or spaces as a repeat', () => {
    const rows = markRepeats([row({ name: 'Setup.pdf' }), row({ name: 'other.md' }), row({ name: ' setup.pdf' })]);
    expect(rows.map((r) => !!r.repeat)).toEqual([false, false, true]);
  });
});

describe('choices', () => {
  it('offers each row its choices with the spec defaults', () => {
    const version = row({ action: 'version', document: doc(), version: 3 });
    expect(choicesFor(version).map((c) => c.id)).toEqual(['version', 'separate', 'skip']);
    expect(choicesFor(version)[0].label).toBe('New version of setup.pdf');
    expect(defaultChoice(version)).toBe('version');

    const older = row({ action: 'older_version', document: doc(), version: 1 });
    expect(choicesFor(older).map((c) => c.label)).toEqual(['Leave alone', 'Restore as current']);
    expect(defaultChoice(older)).toBe('leave');

    const ambiguous = row({ action: 'ambiguous', candidates: [
      { id: 'c1', name: 'README.md', version: 3, added_by: { id: 'u', name: 'Alice' }, added_at: '2026-11-02T10:00:00Z' },
      { id: 'c2', name: 'README.md', version: 1, added_by: null, added_at: '2026-11-03T10:00:00Z' }] });
    const labels = choicesFor(ambiguous).map((c) => c.label);
    expect(choicesFor(ambiguous).map((c) => c.id)).toEqual(['skip', 'doc:c1', 'doc:c2', 'separate']);
    expect(labels[1].startsWith('v3 · added by Alice · ')).toBe(true);
    expect(labels[2].startsWith('v1 · added by someone no longer here · ')).toBe(true);
    expect(defaultChoice(ambiguous)).toBe('skip');

    const repeat = row({ action: 'version', document: doc(), repeat: true });
    expect(choicesFor(repeat).map((c) => c.id)).toEqual(['skip', 'separate']);
    expect(defaultChoice(repeat)).toBe('skip');

    for (const action of ['new', 'unchanged', 'refused']) {
      expect(choicesFor(row({ action }))).toEqual([]);
      expect(defaultChoice(row({ action }))).toBe(null);
    }
    expect(choicesFor(row({ action: 'unchanged', repeat: true, document: doc() }))).toEqual([]);
  });

  it('sends nothing for unchanged, refused, skip and leave', () => {
    expect(requestFor(row({ action: 'unchanged' }), null)).toBe(null);
    expect(requestFor(row({ action: 'refused', code: 'too_large' }), null)).toBe(null);
    expect(requestFor(row({ action: 'version' }), 'skip')).toBe(null);
    expect(requestFor(row({ action: 'older_version' }), 'leave')).toBe(null);
  });

  it('turns each sending choice into the upload fields', () => {
    expect(requestFor(row({ action: 'new' }), null)).toEqual({ on_match: 'version', on_older: 'leave' });
    expect(requestFor(row({ action: 'version' }), 'version')).toEqual({ on_match: 'version', on_older: 'leave' });
    expect(requestFor(row({ action: 'version' }), 'separate')).toEqual({ on_match: 'separate', on_older: 'leave' });
    expect(requestFor(row({ action: 'older_version' }), 'restore')).toEqual({ on_match: 'version', on_older: 'restore' });
    expect(requestFor(row({ action: 'ambiguous' }), 'doc:abc'))
      .toEqual({ on_match: 'version', on_older: 'leave', document_id: 'abc' });
  });

  it('counts the summary and the bytes to send', () => {
    const rows = [
      row({ action: 'new', size: 100 }), row({ action: 'version', size: 200 }), row({ action: 'version', size: 5 }),
      row({ action: 'unchanged', size: 7 }), row({ action: 'older_version', size: 9 }),
      row({ action: 'older_version', size: 11 }), row({ action: 'refused', size: 13 }),
      row({ action: 'ambiguous', size: 17 }),
    ];
    const choices = [null, 'version', 'separate', null, 'leave', 'restore', null, 'skip'];
    expect(summarize(rows, choices)).toEqual({ new: 2, versions: 1, restored: 1, unchanged: 1, leftAlone: 1,
      skipped: 1, refused: 1, toSend: 4, bytesToSend: 316 });
  });
});
