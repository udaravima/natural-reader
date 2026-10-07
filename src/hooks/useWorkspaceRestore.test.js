import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useWorkspaceRestore } from './useWorkspaceRestore';

// v2.2 Task A: the saved workspace belongs to the signed-in user, so it is
// looked up only once that user is known, and again for a new user.
describe('useWorkspaceRestore', () => {
    it('does nothing until the reader is ready and a user is known', async () => {
        const getSaved = vi.fn(async () => ({ rootName: 'notes' }));
        const onSaved = vi.fn();
        const { rerender } = renderHook((p) => useWorkspaceRestore(p),
            { initialProps: { ready: false, userId: 'u1', getSaved, onSaved } });
        rerender({ ready: true, userId: null, getSaved, onSaved });
        await new Promise((r) => setTimeout(r, 10));
        expect(getSaved).not.toHaveBeenCalled();

        rerender({ ready: true, userId: 'u1', getSaved, onSaved });
        await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ rootName: 'notes' }, 'u1'));
    });

    it('looks again for a new user, and a lookup the new user superseded is dropped', async () => {
        let finishFirst;
        const getSaved = vi.fn()
            .mockImplementationOnce(() => new Promise((r) => { finishFirst = r; }))
            .mockImplementationOnce(async () => ({ rootName: 'B-notes' }));
        const onSaved = vi.fn();
        const { rerender } = renderHook((p) => useWorkspaceRestore(p),
            { initialProps: { ready: true, userId: 'userA', getSaved, onSaved } });
        rerender({ ready: true, userId: 'userB', getSaved, onSaved });
        await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ rootName: 'B-notes' }, 'userB'));
        finishFirst({ rootName: 'A-notes' });
        await new Promise((r) => setTimeout(r, 10));
        expect(onSaved).toHaveBeenCalledTimes(1);
    });

    it('a failed lookup is logged, not thrown', async () => {
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        const getSaved = vi.fn(async () => { throw new Error('idb gone'); });
        renderHook(() => useWorkspaceRestore({ ready: true, userId: 'u1', getSaved, onSaved: vi.fn() }));
        await waitFor(() => expect(warn).toHaveBeenCalled());
        warn.mockRestore();
    });
});
