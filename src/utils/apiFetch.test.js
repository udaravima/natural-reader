import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiFetch, setUnauthorizedHandler } from './apiFetch';

describe('apiFetch', () => {
  beforeEach(() => { globalThis.fetch = vi.fn(); setUnauthorizedHandler(null); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('sends credentials:include and builds a same-origin URL when host is blank', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await apiFetch('', '', '/v1/auth/me');
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/v1/auth/me',
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('builds an absolute URL from host+port and preserves method/body', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await apiFetch('localhost', '8000', '/v1/docs', { method: 'POST', body: 'x' });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/v1/docs',
      expect.objectContaining({ credentials: 'include', method: 'POST', body: 'x' }),
    );
  });

  it('invokes the unauthorized handler on 401 and still returns the response', async () => {
    globalThis.fetch.mockResolvedValue(new Response('', { status: 401 }));
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    const res = await apiFetch('', '', '/v1/docs');
    expect(onUnauth).toHaveBeenCalledOnce();
    expect(res.status).toBe(401);
  });

  it('does NOT invoke the handler on a 200', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    await apiFetch('', '', '/v1/docs');
    expect(onUnauth).not.toHaveBeenCalled();
  });
});
