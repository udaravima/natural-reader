import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { projectsApi } from './projectsApi';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const api = projectsApi('', '');

describe('projectsApi — documents and versions (V1)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('fileDoc returns the filing, PUTting with no query by default', async () => {
    apiFetch.mockResolvedValue(json(201, { document_id: 'x', version: 1 }));
    expect(await api.fileDoc('p1', 'd1')).toEqual({ filed: { document_id: 'x', version: 1 } });
    expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p1/docs/d1', { method: 'PUT' });
  });

  it('fileDoc sends the choice as a query', async () => {
    apiFetch.mockResolvedValue(json(201, { document_id: 'x', version: 2 }));
    await api.fileDoc('p1', 'd1', { asVersionOf: 'x' });
    expect(apiFetch.mock.calls[0][2]).toBe('/v1/projects/p1/docs/d1?as_version_of=x');
    await api.fileDoc('p1', 'd1', { keepBoth: true });
    expect(apiFetch.mock.calls[1][2]).toBe('/v1/projects/p1/docs/d1?keep_both=true');
  });

  it.each(['name_exists', 'older_version'])('fileDoc returns a %s conflict instead of throwing', async (error) => {
    const detail = { error, message: 'm', document: { id: 'x', name: 'a.pdf', version: 2 }, version: 1 };
    apiFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail }) });
    expect(await api.fileDoc('p1', 'd1')).toEqual({ conflict: detail });
  });

  it('fileDoc throws the notice for any other refusal', async () => {
    apiFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: { error: 'current_elsewhere', message: 'Merge the two instead.' } }) });
    await expect(api.fileDoc('p1', 'd1')).rejects.toThrow('Merge the two instead.');
  });

  it('uploadVersion posts multipart with the note', async () => {
    apiFetch.mockResolvedValue(json(201, { version: 2, doc_id: 'h', restored_from: null, state: 'indexing' }));
    const file = new File(['x'], 'r.pdf');
    await api.uploadVersion('p1', 'x', file, 'fixed');
    const [, , path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/v1/projects/p1/documents/x/versions');
    expect(opts.method).toBe('POST');
    expect(opts.body.get('file')).toBeInstanceOf(File);
    expect(opts.body.get('note')).toBe('fixed');
    expect(opts.headers).toBeUndefined(); // the browser sets the multipart boundary
  });

  it('the other calls hit the documented routes', async () => {
    apiFetch.mockResolvedValue(json(200, []));
    await api.documents('p1');
    await api.versions('p1', 'x');
    await api.versionFromLibrary('p1', 'x', 'h', '');
    await api.restoreVersion('p1', 'x', 1);
    await api.mergeDocuments('p1', 'x', 'y');
    apiFetch.mockResolvedValue(json(204, null));
    await api.deleteVersion('p1', 'x', 1);
    await api.removeDocument('p1', 'x');
    expect(apiFetch.mock.calls.map(([, , path, o]) => `${o?.method || 'GET'} ${path}`)).toEqual([
      'GET /v1/projects/p1/documents',
      'GET /v1/projects/p1/documents/x/versions',
      'POST /v1/projects/p1/documents/x/versions/from-library',
      'POST /v1/projects/p1/documents/x/versions/1/restore',
      'POST /v1/projects/p1/documents/x/merge',
      'DELETE /v1/projects/p1/documents/x/versions/1',
      'DELETE /v1/projects/p1/documents/x',
    ]);
    expect(JSON.parse(apiFetch.mock.calls[2][3].body)).toEqual({ doc_id: 'h', note: null });
    expect(JSON.parse(apiFetch.mock.calls[4][3].body)).toEqual({ from: 'y' });
  });
});
