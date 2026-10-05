import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDocUse } from './useDocUse';
import { docUseKey } from '../db';

beforeEach(() => localStorage.clear());

describe('useDocUse — "Use this document", per user and per chat (v2.4 Task E)', () => {
    it('is on by default', () => {
        const { result } = renderHook(() => useDocUse('u1', 's1'));
        expect(result.current.enabled).toBe(true);
    });

    it('stays off for that chat only, and survives a remount', () => {
        const { result, unmount } = renderHook(() => useDocUse('u1', 's1'));
        act(() => result.current.setEnabled(false));
        expect(result.current.enabled).toBe(false);
        expect(result.current.enabledFor('s2')).toBe(true);
        unmount();
        expect(renderHook(() => useDocUse('u1', 's1')).result.current.enabled).toBe(false);
    });

    it('never shows one user\'s choice to another', () => {
        const a = renderHook(() => useDocUse('u1', 's1'));
        act(() => a.result.current.setEnabled(false));
        expect(renderHook(() => useDocUse('u2', 's1')).result.current.enabled).toBe(true);
    });

    it('carries a new chat\'s choice to the session it gets', () => {
        const { result } = renderHook(() => useDocUse('u1', null));
        act(() => result.current.setEnabled(false));
        expect(result.current.enabled).toBe(false);
        act(() => result.current.adopt('s9'));
        expect(result.current.enabledFor('s9')).toBe(false);
        expect(JSON.parse(localStorage.getItem(docUseKey('u1')))).toEqual({ s9: false });
        expect(result.current.enabledFor(null)).toBe(true);        // the next new chat starts on
    });

    it('stores nothing without a signed-in user', () => {
        const { result } = renderHook(() => useDocUse(null, 's1'));
        act(() => result.current.setEnabled(false));
        expect(localStorage.length).toBe(0);
    });

    it('turning it back on removes the stored choice', () => {
        const { result } = renderHook(() => useDocUse('u1', 's1'));
        act(() => result.current.setEnabled(false));
        act(() => result.current.setEnabled(true));
        expect(JSON.parse(localStorage.getItem(docUseKey('u1')))).toEqual({});
    });
});
