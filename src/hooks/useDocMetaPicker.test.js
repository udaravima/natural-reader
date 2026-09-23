import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useDocMetaPicker } from './useDocMetaPicker';

describe('useDocMetaPicker', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useDocMetaPicker('a.pdf'));
    expect(result.current.projectId).toBe('');
    expect(result.current.tagsText).toBe('');
  });

  it('holds a chosen project + tags while the same document stays loaded', () => {
    const { result, rerender } = renderHook(({ key }) => useDocMetaPicker(key), {
      initialProps: { key: 'a.pdf' },
    });
    act(() => {
      result.current.setProjectId('p1');
      result.current.setTagsText('x, y');
    });
    rerender({ key: 'a.pdf' }); // same document — selection must persist
    expect(result.current.projectId).toBe('p1');
    expect(result.current.tagsText).toBe('x, y');
  });

  it('resets the picker when a different document is loaded (no cross-doc leak)', () => {
    const { result, rerender } = renderHook(({ key }) => useDocMetaPicker(key), {
      initialProps: { key: 'a.pdf' },
    });
    act(() => {
      result.current.setProjectId('p1');
      result.current.setTagsText('x');
    });
    rerender({ key: 'b.pdf' }); // switching documents clears the selection
    expect(result.current.projectId).toBe('');
    expect(result.current.tagsText).toBe('');
  });
});
