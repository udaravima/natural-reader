import { describe, it, expect } from 'vitest';
import { ownedBy, visibleTo } from './visibleTo';

describe('visibleTo', () => {
    it('shows owned state only to its owner', () => {
        const ws = ownedBy('userA', { rootName: 'notes' });
        expect(visibleTo(ws, 'userA')).toEqual({ rootName: 'notes' });
        expect(visibleTo(ws, 'userB')).toBeNull();
        expect(visibleTo(ws, null)).toBeNull();          // no one signed in
    });

    it('nothing owned is nothing visible', () => {
        expect(ownedBy('userA', null)).toBeNull();
        expect(visibleTo(null, 'userA')).toBeNull();
    });
});
