import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';
import { renderHook, act } from '@testing-library/react';
import { useUserDraft } from './useUserDraft';
import { setLibraryOwner } from '../db';

// v2.2 final review: the unsent chat draft is user content, so it belongs to
// the signed-in user like the reading positions.
describe('useUserDraft', () => {
    beforeEach(async () => { localStorage.clear(); await setLibraryOwner(null); });

    it('keeps each user\'s draft apart, across a reload', () => {
        const { result, rerender } = renderHook(({ userId }) => useUserDraft(userId), { initialProps: { userId: 'userA' } });
        act(() => { result.current[1]('half-typed by A'); });
        expect(result.current[0]).toBe('half-typed by A');

        rerender({ userId: 'userB' });
        expect(result.current[0]).toBe('');
        act(() => { result.current[1]((d) => d || 'B quoted text'); });
        expect(result.current[0]).toBe('B quoted text');

        const reloaded = renderHook(() => useUserDraft('userA'));
        expect(reloaded.result.current[0]).toBe('half-typed by A');
    });

    it('with no user known there is no draft, and typing saves nothing', () => {
        const { result } = renderHook(() => useUserDraft(null));
        act(() => { result.current[1]('before sign-in'); });
        expect(result.current[0]).toBe('');
        expect(Object.keys(localStorage).filter((k) => k.includes('chatDraft'))).toEqual([]);
    });

    it('a pre-release draft goes to the first user who signs in, once', async () => {
        localStorage.setItem('neural-pdf-chatDraft', JSON.stringify('old draft'));
        await setLibraryOwner('userA', { claimLegacy: true });
        expect(renderHook(() => useUserDraft('userA')).result.current[0]).toBe('old draft');
        expect(localStorage.getItem('neural-pdf-chatDraft')).toBeNull();
        await setLibraryOwner('userB', { claimLegacy: true });
        expect(renderHook(() => useUserDraft('userB')).result.current[0]).toBe('');
    });
});
