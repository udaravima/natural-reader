import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiFetch, setUnauthorizedHandler, setForbiddenHandler } from './apiFetch';

describe('apiFetch', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
    setUnauthorizedHandler(null);
    setForbiddenHandler(null);
  });
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

  it('invokes the forbidden handler on a 403 with detail.error=missing_capability and still returns the response', async () => {
    globalThis.fetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: { error: 'missing_capability', capability: 'chat' } }), { status: 403 })
    );
    const onForbidden = vi.fn();
    setForbiddenHandler(onForbidden);
    const res = await apiFetch('', '', '/v1/chat');
    expect(onForbidden).toHaveBeenCalledOnce();
    expect(res.status).toBe(403);
  });

  it('does NOT invoke the forbidden handler on a 403 without the missing_capability shape', async () => {
    globalThis.fetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: { status: 'pending' } }), { status: 403 })
    );
    const onForbidden = vi.fn();
    setForbiddenHandler(onForbidden);
    await apiFetch('', '', '/v1/auth/me');
    expect(onForbidden).not.toHaveBeenCalled();
  });

  it('does NOT invoke the forbidden handler on a 200', async () => {
    globalThis.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    const onForbidden = vi.fn();
    setForbiddenHandler(onForbidden);
    await apiFetch('', '', '/v1/docs');
    expect(onForbidden).not.toHaveBeenCalled();
  });

  it('does not throw when a 403 body is not valid JSON', async () => {
    globalThis.fetch.mockResolvedValue(new Response('not json', { status: 403 }));
    const onForbidden = vi.fn();
    setForbiddenHandler(onForbidden);
    const res = await apiFetch('', '', '/v1/docs');
    expect(onForbidden).not.toHaveBeenCalled();
    expect(res.status).toBe(403);
  });
});
