import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { registerDocument, parseTagsInput } from './docMeta';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const baseArgs = {
  apiHost: '', apiPort: '',
  docId: 'd1', fileName: 'a.pdf', fileType: 'pdf', sizeBytes: 123, pageCount: 5,
};

describe('registerDocument', () => {
  beforeEach(() => vi.clearAllMocks());

  it('PATCHes tags and PUTs the project link after register', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/docs/d1' && opts?.method === 'PATCH') return json(200, { doc_id: 'd1' });
      if (path === '/v1/projects/p1/docs/d1' && opts?.method === 'PUT') return json(204, {});
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a', 'b'] });

    const [postCall, patchCall, putCall] = apiFetch.mock.calls;
    expect(apiFetch).toHaveBeenCalledTimes(3);
    expect(postCall[2]).toBe('/v1/docs');
    expect(patchCall[2]).toBe('/v1/docs/d1');
    expect(JSON.parse(patchCall[3].body)).toEqual({ tags: ['a', 'b'] });
    expect(putCall[2]).toBe('/v1/projects/p1/docs/d1');
    expect(putCall[3].method).toBe('PUT');
  });

  it('only PUTs the link when tags are empty (no PATCH)', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/projects/p1/docs/d1' && opts?.method === 'PUT') return json(204, {});
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: 'p1', tags: [] });

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(apiFetch.mock.calls[1][2]).toBe('/v1/projects/p1/docs/d1');
    expect(apiFetch.mock.calls.some(([, , , o]) => o?.method === 'PATCH')).toBe(false);
  });

  it('logs but does not throw when the link fails, and still sends tags', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/docs/d1' && opts?.method === 'PATCH') return json(200, { doc_id: 'd1' });
      if (opts?.method === 'PUT') return json(404, {});
      return json(404, {});
    });

    await expect(registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a'] })).resolves.toBeDefined();
    expect(apiFetch.mock.calls.some(([, , p, o]) => p === '/v1/docs/d1' && o?.method === 'PATCH')).toBe(true);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('throws when the register call fails, without attempting follow-ups', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    await expect(registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a'] })).rejects.toThrow('HTTP 500');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('issues NO PATCH when neither a project nor tags were chosen', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: '', tags: [] });

    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs', expect.objectContaining({ method: 'POST' }));
  });
});

describe('parseTagsInput', () => {
  it('splits, trims, and dedupes comma-separated tags', () => {
    expect(parseTagsInput('foo, bar , foo,,baz')).toEqual(['foo', 'bar', 'baz']);
  });

  it('returns an empty array for blank input', () => {
    expect(parseTagsInput('')).toEqual([]);
    expect(parseTagsInput('   ')).toEqual([]);
    expect(parseTagsInput(undefined)).toEqual([]);
  });
});
