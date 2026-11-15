import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiFetch, apiUpload, setUnauthorizedHandler, setForbiddenHandler } from './apiFetch';

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

describe('apiUpload', () => {
  let sent;
  class FakeXHR {
    constructor() { this.upload = {}; this.headers = {}; sent = this; }
    open(method, url) { this.method = method; this.url = url; }
    send(body) { this.body = body; }
    abort() { this.onabort?.(); }
  }
  beforeEach(() => { vi.stubGlobal('XMLHttpRequest', FakeXHR); });
  afterEach(() => { vi.unstubAllGlobals(); setUnauthorizedHandler(null); setForbiddenHandler(null); });

  it('posts the form with credentials, reports progress and resolves with the parsed body', async () => {
    const onProgress = vi.fn();
    const form = new FormData();
    const p = apiUpload('', '', '/v1/projects/p1/uploads', form, { onProgress });
    expect([sent.method, sent.url, sent.withCredentials, sent.body]).toEqual(['POST', '/v1/projects/p1/uploads', true, form]);
    sent.upload.onprogress({ lengthComputable: true, loaded: 32, total: 50 });
    expect(onProgress).toHaveBeenCalledWith(64);
    sent.status = 201; sent.responseText = '{"result":"created"}'; sent.onload();
    expect(await p).toEqual({ ok: true, status: 201, body: { result: 'created' } });
  });

  it('calls the unauthorized handler on 401 and the forbidden one on missing_capability', async () => {
    const onUnauthorized = vi.fn();
    const onForbidden = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setForbiddenHandler(onForbidden);
    let p = apiUpload('', '', '/x', new FormData());
    sent.status = 401; sent.responseText = ''; sent.onload();
    expect(await p).toEqual({ ok: false, status: 401, body: null });
    p = apiUpload('', '', '/x', new FormData());
    sent.status = 403; sent.responseText = '{"detail":{"error":"missing_capability"}}'; sent.onload();
    await p;
    expect([onUnauthorized.mock.calls.length, onForbidden.mock.calls.length]).toEqual([1, 1]);
  });

  it('resolves with status 0 when the connection fails', async () => {
    const p = apiUpload('', '', '/x', new FormData());
    sent.onerror();
    expect(await p).toEqual({ ok: false, status: 0, body: null });
  });
});
