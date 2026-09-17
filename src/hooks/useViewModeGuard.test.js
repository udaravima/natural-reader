import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useViewModeGuard } from './useViewModeGuard';

describe('useViewModeGuard', () => {
  it('coerces a persisted admin viewMode to reader for a member once auth resolves', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'admin', setViewMode, authState: 'active', role: 'member',
    }));
    expect(setViewMode).toHaveBeenCalledWith('reader');
  });

  it('keeps the admin view for an actual admin', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'admin', setViewMode, authState: 'active', role: 'admin',
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });

  it('waits for the auth probe before coercing (boot race guard)', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'admin', setViewMode, authState: 'loading', role: null,
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });

  it('leaves non-admin view modes alone', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'chat', setViewMode, authState: 'active', role: 'member',
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });
});
