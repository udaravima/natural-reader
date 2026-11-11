import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

vi.mock('../lib/notifications', () => ({ fetchUnread: vi.fn() }));
import { fetchUnread } from '../lib/notifications';
import { useNotifications, POLL_MS } from './useNotifications';

let visibility = 'visible';
const setVisible = (v) => {
    visibility = v;
    document.dispatchEvent(new Event('visibilitychange'));
};

// Let the hook's awaited fetch settle inside act().
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe('useNotifications', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.clearAllMocks();
        visibility = 'visible';
        vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
        fetchUnread.mockResolvedValue({ count: 2, action: 1 });
    });
    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('asks at once, then every 60 s while visible', async () => {
        expect(POLL_MS).toBe(60_000);
        const { result } = renderHook(() => useNotifications('', '', true));
        await flush();
        expect(fetchUnread).toHaveBeenCalledTimes(1);
        expect(result.current.count).toBe(2);
        expect(result.current.action).toBe(1);
        await act(async () => { vi.advanceTimersByTime(60_000); });
        await flush();
        expect(fetchUnread).toHaveBeenCalledTimes(2);
    });

    it('does not poll while hidden, and asks at once on becoming visible', async () => {
        renderHook(() => useNotifications('', '', true));
        await flush();
        act(() => setVisible('hidden'));
        await act(async () => { vi.advanceTimersByTime(5 * 60_000); });
        expect(fetchUnread).toHaveBeenCalledTimes(1);
        act(() => setVisible('visible'));
        await flush();
        expect(fetchUnread).toHaveBeenCalledTimes(2);
    });

    it('does nothing while disabled (signed out)', async () => {
        const { result } = renderHook(() => useNotifications('', '', false));
        await act(async () => { vi.advanceTimersByTime(3 * 60_000); });
        expect(fetchUnread).not.toHaveBeenCalled();
        expect(result.current.count).toBe(0);
    });

    it('refresh() asks at once', async () => {
        const { result } = renderHook(() => useNotifications('', '', true));
        await flush();
        fetchUnread.mockResolvedValue({ count: 5, action: 0 });
        await act(async () => { result.current.refresh(); });
        await flush();
        expect(fetchUnread).toHaveBeenCalledTimes(2);
        expect(result.current.count).toBe(5);
    });

    it('a failed request keeps the last counts', async () => {
        const { result } = renderHook(() => useNotifications('', '', true));
        await flush();
        fetchUnread.mockResolvedValue(null);
        await act(async () => { vi.advanceTimersByTime(60_000); });
        await flush();
        expect(result.current.count).toBe(2);
    });

    it('stops polling when unmounted', async () => {
        const { unmount } = renderHook(() => useNotifications('', '', true));
        await flush();
        unmount();
        await act(async () => { vi.advanceTimersByTime(5 * 60_000); });
        expect(fetchUnread).toHaveBeenCalledTimes(1);
    });
});
