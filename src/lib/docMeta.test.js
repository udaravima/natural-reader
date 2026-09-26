import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { registerDocument, parseTagsInput } from './docMeta';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const baseArgs = {
  apiHost: '', apiPort: '',
  file: new Blob(['hello']), fileName: 'a.pdf', clientDocId: 'd1',
};

describe('registerDocument', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs a FormData body with file, file_name, client_doc_id and one tags entry per tag', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(202, { doc_id: 'd1', dedup: false, state: 'stored' });
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, tags: ['a', 'b'] });

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [, , path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/v1/docs');
    expect(opts.method).toBe('POST');
    const form = opts.body;
    expect(form).toBeInstanceOf(FormData);
    expect(form.get('file_name')).toBe('a.pdf');
    expect(form.get('client_doc_id')).toBe('d1');
    expect(form.getAll('tags')).toEqual(['a', 'b']);
    expect(form.get('file')).toBeInstanceOf(Blob);
  });

  it('PUTs the project link after the POST when projectId is set', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(202, { doc_id: 'd1', dedup: false, state: 'stored' });
      if (path === '/v1/projects/p1/docs/d1' && opts?.method === 'PUT') return json(204, {});
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, tags: [], projectId: 'p1' });

    expect(apiFetch).toHaveBeenCalledTimes(2);
    const [postCall, putCall] = apiFetch.mock.calls;
    expect(postCall[2]).toBe('/v1/docs');
    expect(putCall[2]).toBe('/v1/projects/p1/docs/d1');
    expect(putCall[3].method).toBe('PUT');
  });

  it('does not PUT the project link when linkProject is false', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(202, { doc_id: 'd1', dedup: false, state: 'stored' });
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, tags: [], projectId: 'p1', linkProject: false });

    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('there is no PATCH anywhere in the flow', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(202, { doc_id: 'd1', dedup: false, state: 'stored' });
      if (path === '/v1/projects/p1/docs/d1' && opts?.method === 'PUT') return json(204, {});
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, tags: ['a'], projectId: 'p1' });

    expect(apiFetch.mock.calls.some(([, , , o]) => o?.method === 'PATCH')).toBe(false);
  });

  it('throws the mapped notice on a refusal (415 unsupported type)', async () => {
    apiFetch.mockResolvedValue({
      ok: false, status: 415, json: async () => ({ detail: { error: 'unsupported_type' } }),
    });
    await expect(registerDocument({ ...baseArgs, tags: [] })).rejects.toThrow('Unsupported file type.');
  });

  it('returns {docId, dedup, state} from the server response', async () => {
    apiFetch.mockResolvedValue(json(200, { doc_id: 'server-id', dedup: true, state: 'indexed' }));
    const result = await registerDocument({ ...baseArgs, tags: [] });
    expect(result).toEqual({ docId: 'server-id', dedup: true, state: 'indexed' });
  });

  it('logs but does not throw when the project link fails', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(202, { doc_id: 'd1', dedup: false, state: 'stored' });
      if (opts?.method === 'PUT') return json(404, {});
      return json(404, {});
    });

    await expect(registerDocument({ ...baseArgs, tags: [], projectId: 'p1' })).resolves.toBeDefined();
    expect(err).toHaveBeenCalled();
    err.mockRestore();
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
