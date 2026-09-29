import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('../db', () => ({ setLibraryOwner: vi.fn(() => Promise.resolve()) }));

import { useAuth } from './useAuth';
import { setLibraryOwner } from '../db';
import { apiFetch } from '../utils/apiFetch';

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

// Which local-library owner useAuth names for each /v1/auth/me outcome, and
// that only a real signed-in id claims the pre-release records.
describe('useAuth → setLibraryOwner', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
    setLibraryOwner.mockClear();
  });

  it('a 200 names the user and claims legacy records', async () => {
    globalThis.fetch.mockResolvedValue(jsonResponse(200, { id: 'u1', email: 'a@x.io', role: 'member' }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    expect(setLibraryOwner).toHaveBeenCalledWith('u1', { claimLegacy: true });
  });

  it.each([
    ['401', () => new Response('', { status: 401 }), 'anonymous'],
    ['403 pending', () => jsonResponse(403, { detail: { status: 'pending' } }), 'pending'],
  ])('a %s clears the owner', async (_label, response, state) => {
    globalThis.fetch.mockResolvedValue(response());
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe(state));
    expect(setLibraryOwner).toHaveBeenLastCalledWith(null);
  });

  it.each([
    ['a 500', () => globalThis.fetch.mockResolvedValue(new Response('', { status: 500 }))],
    ['a network error', () => globalThis.fetch.mockRejectedValue(new Error('network'))],
  ])('%s falls back to "local" without claiming', async (_label, arrange) => {
    arrange();
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('error'));
    expect(setLibraryOwner).toHaveBeenLastCalledWith('local');
    expect(setLibraryOwner).not.toHaveBeenCalledWith('local', expect.anything());
  });

  it('a 401 from any later call clears the owner', async () => {
    globalThis.fetch.mockResolvedValueOnce(jsonResponse(200, { id: 'u1', email: 'a@x.io', role: 'member' }));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('active'));
    globalThis.fetch.mockResolvedValueOnce(new Response('', { status: 401 }));
    await apiFetch('', '', '/v1/docs');
    await waitFor(() => expect(result.current.state).toBe('anonymous'));
    expect(setLibraryOwner).toHaveBeenLastCalledWith(null);
  });
});
