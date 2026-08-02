import { describe, it, expect } from 'vitest';
import { makePin, addPin, removePin, totalPinnedChars, MAX_PINS, PINNED_CONTEXT_CHAR_BUDGET } from './pins';

const pin = (over = {}) => ({ id: 'x', doc_id: 'd1', fileName: 'f.md', page: 1, kind: 'selection', text: 'hello', ...over });

describe('makePin', () => {
    it('builds a pin with a unique id and the given fields', () => {
        const a = makePin({ doc_id: 'd1', fileName: 'f.md', page: 2, kind: 'page', text: 'abc' });
        const b = makePin({ doc_id: 'd1', fileName: 'f.md', page: 2, kind: 'page', text: 'abc' });
        expect(a).toMatchObject({ doc_id: 'd1', fileName: 'f.md', page: 2, kind: 'page', text: 'abc' });
        expect(a.id).toBeTruthy();
        expect(a.id).not.toBe(b.id);
    });
});

describe('addPin', () => {
    it('appends a new pin', () => {
        const res = addPin([], pin());
        expect(res.added).toBe(true);
        expect(res.pins).toHaveLength(1);
    });
    it('is a no-op for a duplicate (same doc_id + kind + text)', () => {
        const existing = [pin({ id: 'a' })];
        const res = addPin(existing, pin({ id: 'b' })); // different id, same content
        expect(res.added).toBe(false);
        expect(res.reason).toBe('duplicate');
        expect(res.pins).toBe(existing);
    });
    it('rejects past MAX_PINS', () => {
        const many = Array.from({ length: MAX_PINS }, (_, i) => pin({ id: String(i), text: `t${i}` }));
        const res = addPin(many, pin({ id: 'new', text: 'unique' }));
        expect(res.added).toBe(false);
        expect(res.reason).toBe('max-pins');
    });
    it('rejects when the total char budget would be exceeded', () => {
        const big = pin({ id: 'big', text: 'a'.repeat(PINNED_CONTEXT_CHAR_BUDGET - 2) });
        const res = addPin([big], pin({ id: 'small', text: 'aaa' }));
        expect(res.added).toBe(false);
        expect(res.reason).toBe('budget');
    });
});

describe('removePin', () => {
    it('drops the pin with the matching id', () => {
        expect(removePin([pin({ id: 'a' }), pin({ id: 'b', text: 'y' })], 'a')).toEqual([pin({ id: 'b', text: 'y' })]);
    });
});

describe('totalPinnedChars', () => {
    it('sums text lengths', () => {
        expect(totalPinnedChars([pin({ text: 'ab' }), pin({ text: 'cde' })])).toBe(5);
    });
});
