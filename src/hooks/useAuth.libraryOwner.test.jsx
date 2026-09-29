import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

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

  it('refresh (the Retry button) shows loading again, then settles', async () => {
    globalThis.fetch.mockRejectedValueOnce(new Error('network'));
    const { result } = renderHook(() => useAuth('', ''));
    await waitFor(() => expect(result.current.state).toBe('error'));

    let finish;
    globalThis.fetch.mockReturnValueOnce(new Promise((r) => { finish = r; }));
    act(() => { result.current.refresh(); });
    expect(result.current.state).toBe('loading');
    await act(async () => { finish(jsonResponse(200, { id: 'u1', email: 'a@x.io', role: 'member' })); });
    await waitFor(() => expect(result.current.state).toBe('active'));
  });

  it('the first probe starts in loading without setting it again', async () => {
    globalThis.fetch.mockResolvedValue(jsonResponse(200, { id: 'u1', email: 'a@x.io', role: 'member' }));
    const states = [];
    const { result } = renderHook(() => { const a = useAuth('', ''); states.push(a.state); return a; });
    await waitFor(() => expect(result.current.state).toBe('active'));
    expect(states[0]).toBe('loading');
    expect(states.filter((s, i) => i > 0 && s === 'loading' && states[i - 1] !== 'loading')).toEqual([]);
  });

  it('a probe superseded by a host change applies neither its state nor its library owner', async () => {
    let finishOld;
    globalThis.fetch.mockImplementation((url) => (url.includes('old.example.com')
      ? new Promise((r) => { finishOld = r; })
      : Promise.resolve(jsonResponse(200, { id: 'new-user', email: 'n@x.io', role: 'member' }))));
    const { result, rerender } = renderHook(({ host }) => useAuth(host, '443'), { initialProps: { host: 'old.example.com' } });
    rerender({ host: 'new.example.com' });
    await waitFor(() => expect(result.current.user?.id).toBe('new-user'));

    await act(async () => { finishOld(jsonResponse(200, { id: 'old-user', email: 'o@x.io', role: 'member' })); });
    expect(result.current.user.id).toBe('new-user');
    expect(setLibraryOwner).not.toHaveBeenCalledWith('old-user', expect.anything());
  });

  it('a refresh started while the first probe is in flight wins over it', async () => {
    let finishFirst;
    globalThis.fetch.mockReturnValueOnce(new Promise((r) => { finishFirst = r; }));
    globalThis.fetch.mockResolvedValueOnce(new Response('', { status: 401 }));
    const { result } = renderHook(() => useAuth('', ''));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.state).toBe('anonymous');

    await act(async () => { finishFirst(jsonResponse(200, { id: 'late', email: 'l@x.io', role: 'member' })); });
    expect(result.current.state).toBe('anonymous');
    expect(setLibraryOwner).not.toHaveBeenCalledWith('late', expect.anything());
  });
});
