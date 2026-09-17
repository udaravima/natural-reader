import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useAuth } from './useAuth';

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('useAuth', () => {
  beforeEach(() => { globalThis.fetch = vi.fn(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('resolves to active with the user on a 200 /me', async () => {
    globalThis.fetch.mockResolvedValue(jsonResponse(200, { id: '1', email: 'a@x.io', role: 'admin' }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    expect(result.current.user).toEqual({ id: '1', email: 'a@x.io', role: 'admin' });
  });

  it('resolves to anonymous on 401', async () => {
    globalThis.fetch.mockResolvedValue(new Response('', { status: 401 }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('anonymous'));
  });

  it('resolves to pending on a 403 with detail.status=pending', async () => {
    globalThis.fetch.mockResolvedValue(jsonResponse(403, { detail: { status: 'pending' } }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('pending'));
  });

  it('resolves to disabled on a 403 with detail.status=disabled', async () => {
    globalThis.fetch.mockResolvedValue(jsonResponse(403, { detail: { status: 'disabled' } }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('disabled'));
  });

  it('resolves to error when the request throws', async () => {
    globalThis.fetch.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('error'));
  });
});
