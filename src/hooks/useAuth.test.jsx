import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useAuth } from './useAuth';
import { apiFetch } from '../utils/apiFetch';

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('useAuth', () => {
  beforeEach(() => { globalThis.fetch = vi.fn(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('resolves to active with the user on a 200 /me', async () => {
    globalThis.fetch.mockResolvedValue(jsonResponse(200, { id: '1', email: 'a@x.io', role: 'admin' }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    expect(result.current.user).toEqual({ id: '1', email: 'a@x.io', role: 'admin', capabilities: [] });
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

  it('exposes capabilities from /v1/auth/me', async () => {
    globalThis.fetch.mockResolvedValue(
      jsonResponse(200, { id: 'u', email: 'e@x.io', role: 'member', capabilities: ['reader'] })
    );
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    expect(result.current.user.capabilities).toEqual(['reader']);
  });

  it('re-probes /v1/auth/me when a 403 missing_capability surfaces from any apiFetch call', async () => {
    const meBody = { id: 'u', email: 'e@x.io', role: 'member', capabilities: ['reader'] };
    globalThis.fetch.mockImplementation((url) => {
      if (url === '/v1/auth/me') return Promise.resolve(jsonResponse(200, meBody));
      return Promise.resolve(jsonResponse(403, { detail: { error: 'missing_capability', capability: 'chat' } }));
    });

    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    const meCallsBefore = globalThis.fetch.mock.calls.filter(([url]) => url === '/v1/auth/me').length;
    expect(meCallsBefore).toBe(1);

    // A 403 missing_capability from some unrelated apiFetch call elsewhere in
    // the app should trigger useAuth's registered forbidden handler, which
    // re-runs the /me probe — this is the seam apiFetch.js exercises directly;
    // here we verify useAuth actually wires itself up to it end-to-end.
    await apiFetch('', '', '/v1/chat');

    await waitFor(() => {
      const meCallsAfter = globalThis.fetch.mock.calls.filter(([url]) => url === '/v1/auth/me').length;
      expect(meCallsAfter).toBeGreaterThan(meCallsBefore);
    });
  });
});
