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

  it('issues a PATCH carrying project_id and tags after a chosen project + tags', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/docs/d1' && opts?.method === 'PATCH') return json(200, { doc_id: 'd1' });
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a', 'b'] });

    // Real sequencing: register first, then the follow-up PATCH.
    expect(apiFetch).toHaveBeenCalledTimes(2);
    const [postCall, patchCall] = apiFetch.mock.calls;
    expect(postCall[2]).toBe('/v1/docs');
    expect(postCall[3].method).toBe('POST');
    expect(patchCall[2]).toBe('/v1/docs/d1');
    expect(patchCall[3].method).toBe('PATCH');
    expect(JSON.parse(patchCall[3].body)).toEqual({ project_id: 'p1', tags: ['a', 'b'] });
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

  it('issues a PATCH with only project_id when tags are empty', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/docs/d1' && opts?.method === 'PATCH') return json(200, { doc_id: 'd1' });
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: 'p1', tags: [] });

    const patchCall = apiFetch.mock.calls[1];
    expect(JSON.parse(patchCall[3].body)).toEqual({ project_id: 'p1' });
  });

  it('throws when the register call fails, without attempting a PATCH', async () => {
    apiFetch.mockImplementation(async () => json(500, {}));

    await expect(registerDocument({ ...baseArgs, projectId: 'p1', tags: [] })).rejects.toThrow('HTTP 500');
    expect(apiFetch).toHaveBeenCalledTimes(1);
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
