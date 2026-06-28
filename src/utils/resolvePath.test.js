import { describe, it, expect } from 'vitest';
import { resolvePath, isExternal, dirname } from './resolvePath';

describe('isExternal', () => {
    it('flags http/https/mailto/protocol-relative', () => {
        expect(isExternal('http://x')).toBe(true);
        expect(isExternal('https://x')).toBe(true);
        expect(isExternal('mailto:a@b.c')).toBe(true);
        expect(isExternal('//cdn/x')).toBe(true);
        expect(isExternal('./a.md')).toBe(false);
        expect(isExternal('a.md')).toBe(false);
    });
});

describe('dirname', () => {
    it('returns parent dir or empty', () => {
        expect(dirname('a/b/c.md')).toBe('a/b');
        expect(dirname('c.md')).toBe('');
    });
});

describe('resolvePath', () => {
    it('classifies empty and anchor-only', () => {
        expect(resolvePath('a', '')).toEqual({ kind: 'none' });
        expect(resolvePath('a', '#Heading%20One')).toEqual({ kind: 'anchor', anchor: 'Heading One' });
    });
    it('classifies external', () => {
        expect(resolvePath('a', 'https://x.com')).toEqual({ kind: 'external', href: 'https://x.com' });
    });
    it('resolves ./ and bare names against currentDir', () => {
        expect(resolvePath('docs', './b.md')).toEqual({ kind: 'path', path: 'docs/b.md', anchor: null });
        expect(resolvePath('docs', 'b.md')).toEqual({ kind: 'path', path: 'docs/b.md', anchor: null });
    });
    it('resolves ../ and nested', () => {
        expect(resolvePath('docs/sub', '../top.md')).toEqual({ kind: 'path', path: 'docs/top.md', anchor: null });
        expect(resolvePath('docs', 'sub/c.md')).toEqual({ kind: 'path', path: 'docs/sub/c.md', anchor: null });
    });
    it('splits #anchor and strips ?query and decodes %20', () => {
        expect(resolvePath('docs', './a%20b.md?x=1#sec')).toEqual({ kind: 'path', path: 'docs/a b.md', anchor: 'sec' });
    });
    it('treats leading slash as workspace-root absolute', () => {
        expect(resolvePath('docs/sub', '/top.md')).toEqual({ kind: 'path', path: 'top.md', anchor: null });
    });
});

describe('resolvePath edge cases (contract)', () => {
    it('clamps ".." above the root to a within-root relative path (safe: callers gate on workspace.hasFile)', () => {
        expect(resolvePath('docs', '../../etc/passwd')).toEqual({ kind: 'path', path: 'etc/passwd', anchor: null });
    });
    it('resolves a bare name against an empty currentDir', () => {
        expect(resolvePath('', 'b.md')).toEqual({ kind: 'path', path: 'b.md', anchor: null });
    });
    it('collapses a trailing slash in currentDir', () => {
        expect(resolvePath('docs/', 'b.md')).toEqual({ kind: 'path', path: 'docs/b.md', anchor: null });
    });
});
