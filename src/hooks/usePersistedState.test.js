import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePersistedState, resetPersisted, runOnce } from './usePersistedState';

// A setting stores only the user's own choice. It used to save its default on
// first load as if chosen, so a changed default (the code's or an admin's)
// never reached a browser that had opened the app once.
describe('usePersistedState', () => {
    beforeEach(() => localStorage.clear());

    it('writes nothing until the user chooses', () => {
        renderHook(() => usePersistedState('k', 'a'));
        expect(localStorage.getItem('neural-pdf-k')).toBeNull();
    });

    it('follows a changing default while unchosen', () => {
        const { result, rerender } = renderHook(({ d }) => usePersistedState('k', d), { initialProps: { d: 'a' } });
        expect(result.current[0]).toBe('a');
        rerender({ d: 'b' });
        expect(result.current[0]).toBe('b');
    });

    it('keeps a choice, stored, over any later default', () => {
        const { result, rerender } = renderHook(({ d }) => usePersistedState('k', d), { initialProps: { d: 'a' } });
        act(() => result.current[1]('mine'));
        rerender({ d: 'b' });
        expect(result.current[0]).toBe('mine');
        expect(JSON.parse(localStorage.getItem('neural-pdf-k'))).toBe('mine');
    });

    it('a choice equal to the default is still a choice', () => {
        const { result, rerender } = renderHook(({ d }) => usePersistedState('k', d), { initialProps: { d: 'a' } });
        act(() => result.current[1]('a'));
        rerender({ d: 'b' });
        expect(result.current[0]).toBe('a');
    });

    it('a functional update starts from the shown value', () => {
        const { result } = renderHook(() => usePersistedState('n', 1));
        act(() => result.current[1]((v) => v + 1));
        expect(result.current[0]).toBe(2);
    });

    it('reads a value stored earlier', () => {
        localStorage.setItem('neural-pdf-k', JSON.stringify('old'));
        const { result } = renderHook(() => usePersistedState('k', 'a'));
        expect(result.current[0]).toBe('old');
    });

    it('resetPersisted drops the choice, and every hook on that key follows the default again', () => {
        const one = renderHook(() => usePersistedState('k', 'a'));
        const two = renderHook(() => usePersistedState('k', 'a'));
        act(() => one.result.current[1]('mine'));
        act(() => resetPersisted('k'));
        expect(one.result.current[0]).toBe('a');
        expect(two.result.current[0]).toBe('a');
        expect(localStorage.getItem('neural-pdf-k')).toBeNull();
    });

    it('runOnce runs a migration a single time per browser', () => {
        let n = 0;
        runOnce('m1', () => { n += 1; });
        runOnce('m1', () => { n += 1; });
        expect(n).toBe(1);
    });
});
