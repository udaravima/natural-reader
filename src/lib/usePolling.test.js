import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePolling } from './usePolling';

let visibility = 'visible';
const setVisibility = (v) => {
  visibility = v;
  document.dispatchEvent(new Event('visibilitychange'));
};

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    visibility = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('calls the function every 2 s while active, and not before the first 2 s', async () => {
    const fn = vi.fn().mockResolvedValue(undefined);
    renderHook(() => usePolling(fn, true));
    expect(fn).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(fn).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('never polls while inactive, and stops when it turns inactive', async () => {
    const fn = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderHook(({ on }) => usePolling(fn, on), { initialProps: { on: false } });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fn).not.toHaveBeenCalled();
    rerender({ on: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(fn).toHaveBeenCalledTimes(1);
    rerender({ on: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('skips ticks while the tab is hidden and reads once when it comes back', async () => {
    const fn = vi.fn().mockResolvedValue(undefined);
    renderHook(() => usePolling(fn, true));
    act(() => setVisibility('hidden'));
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fn).not.toHaveBeenCalled();
    await act(async () => { setVisibility('visible'); });
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('stops on unmount', async () => {
    const fn = vi.fn().mockResolvedValue(undefined);
    const { unmount } = renderHook(() => usePolling(fn, true));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    setVisibility('visible');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('does not start a read while the last one is still out', async () => {
    let release;
    const fn = vi.fn(() => new Promise((resolve) => { release = resolve; }));
    renderHook(() => usePolling(fn, true));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => { release(); await vi.advanceTimersByTimeAsync(2000); });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('uses the latest function without restarting the clock', async () => {
    const first = vi.fn().mockResolvedValue(undefined);
    const second = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderHook(({ fn }) => usePolling(fn, true), { initialProps: { fn: first } });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    rerender({ fn: second });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
