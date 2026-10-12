import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useProjectFiling } from './useProjectFiling';

const target = { projectId: 'p1', projectName: 'Q3', docId: 'd1', label: 'a.pdf' };
const conflict = { error: 'name_exists', document: { id: 'x', name: 'a.pdf', version: 1 } };

describe('useProjectFiling', () => {
  it('files straight through when there is no conflict', async () => {
    const fileDoc = vi.fn(async () => ({ filed: { document_id: 'n', version: 1 } }));
    const onFiled = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled, onError: vi.fn() }));
    await act(() => result.current.file(target));
    expect(fileDoc).toHaveBeenCalledWith('p1', 'd1');
    expect(onFiled).toHaveBeenCalledWith({ document_id: 'n', version: 1 }, target);
    expect(result.current.dialog).toBeNull();
  });

  it('opens the dialog on a conflict and retries with the choice', async () => {
    const fileDoc = vi.fn()
      .mockResolvedValueOnce({ conflict })
      .mockResolvedValueOnce({ filed: { document_id: 'x', version: 2 } });
    const onFiled = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled, onError: vi.fn() }));
    await act(() => result.current.file(target));
    expect(result.current.dialog.conflict).toBe(conflict);
    expect(result.current.dialog.projectName).toBe('Q3');
    await act(() => result.current.dialog.onChoose({ asVersionOf: 'x' }));
    expect(fileDoc).toHaveBeenLastCalledWith('p1', 'd1', { asVersionOf: 'x' });
    expect(onFiled).toHaveBeenCalled();
    expect(result.current.dialog).toBeNull();
  });

  it('cancel files nothing and shows no error', async () => {
    const fileDoc = vi.fn(async () => ({ conflict }));
    const onFiled = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled, onError }));
    await act(() => result.current.file(target));
    act(() => result.current.dialog.onCancel());
    expect(result.current.dialog).toBeNull();
    expect(fileDoc).toHaveBeenCalledTimes(1);
    expect(onFiled).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('prompt opens the dialog for a conflict found elsewhere (the reader upload)', () => {
    const { result } = renderHook(() => useProjectFiling({ fileDoc: vi.fn(), onFiled: vi.fn(), onError: vi.fn() }));
    act(() => result.current.prompt(target, conflict));
    expect(result.current.dialog.conflict).toBe(conflict);
  });

  it('reports other refusals through onError and closes', async () => {
    const fileDoc = vi.fn(async () => { throw new Error('Only Contributors, Maintainers or Owners can do that.'); });
    const onError = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled: vi.fn(), onError }));
    await act(() => result.current.file(target));
    expect(onError).toHaveBeenCalledWith('Only Contributors, Maintainers or Owners can do that.', target);
    expect(result.current.dialog).toBeNull();
  });
});
