import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useVersionStatus } from './useVersionStatus';

const oldItem = { project: { id: 'p', name: 'P' }, document_id: 'x', version: 1, current_version: 2,
  current_doc_id: 'h2', current_name: 'r.pdf', is_current: false };
const reply = (versions_in) => ({ ok: true, json: async () => ({ versions_in }) });

describe('useVersionStatus', () => {
  beforeEach(() => { apiFetch.mockReset(); });

  it('returns the old-version item, and refetches when refreshKey changes', async () => {
    apiFetch.mockResolvedValueOnce(reply([{ ...oldItem, is_current: true }])).mockResolvedValueOnce(reply([oldItem]));
    const { result, rerender } = renderHook((p) => useVersionStatus(p), {
      initialProps: { apiHost: '', apiPort: '', docId: 'd1', enabled: true, refreshKey: 0 },
    });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    expect(result.current).toBeNull();
    rerender({ apiHost: '', apiPort: '', docId: 'd1', enabled: true, refreshKey: 1 });
    await waitFor(() => expect(result.current).toEqual(oldItem));
  });

  it('is null when the fetch fails', async () => {
    apiFetch.mockImplementation(() => Promise.reject(new Error('offline')));
    const { result } = renderHook(() => useVersionStatus({ apiHost: '', apiPort: '', docId: 'd1', enabled: true }));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); // let the rejection settle into the hook
    expect(result.current).toBeNull();
  });

  it('does not fetch when disabled or without a docId', () => {
    renderHook(() => useVersionStatus({ apiHost: '', apiPort: '', docId: 'd1', enabled: false }));
    renderHook(() => useVersionStatus({ apiHost: '', apiPort: '', docId: null, enabled: true }));
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
