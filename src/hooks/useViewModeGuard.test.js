import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useViewModeGuard } from './useViewModeGuard';

describe('useViewModeGuard', () => {
  it('coerces a persisted admin viewMode to reader for a non-admin once auth resolves', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'admin', setViewMode, authState: 'active', caps: ['reader', 'chat'],
    }));
    expect(setViewMode).toHaveBeenCalledWith('reader');
  });

  it('keeps the admin view for an actual admin', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'admin', setViewMode, authState: 'active', caps: ['admin'],
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });

  it('waits for the auth probe before coercing (boot race guard)', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'admin', setViewMode, authState: 'loading', caps: [],
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });

  it('leaves a permitted non-admin view mode alone', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'chat', setViewMode, authState: 'active', caps: ['reader', 'chat'],
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });

  it('coerces chat to reader when the user lacks the chat capability', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'chat', setViewMode, authState: 'active', caps: ['reader'],
    }));
    expect(setViewMode).toHaveBeenCalledWith('reader');
  });

  it('coerces reader to chat when the user lacks the reader capability but has chat', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'reader', setViewMode, authState: 'active', caps: ['chat'],
    }));
    expect(setViewMode).toHaveBeenCalledWith('chat');
  });

  it('coerces reader to admin when the user only has the admin capability', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'reader', setViewMode, authState: 'active', caps: ['admin'],
    }));
    expect(setViewMode).toHaveBeenCalledWith('admin');
  });

  it('does nothing when caps is empty — AuthGate NoAccessScreen covers that case', () => {
    const setViewMode = vi.fn();
    renderHook(() => useViewModeGuard({
      viewMode: 'reader', setViewMode, authState: 'active', caps: [],
    }));
    expect(setViewMode).not.toHaveBeenCalled();
  });
});
