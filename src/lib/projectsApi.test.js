import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn(), apiUpload: vi.fn() }));
import { apiFetch, apiUpload } from '../utils/apiFetch';
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

describe('projectsApi — bulk upload', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uploadPlan posts the files as JSON', async () => {
    apiFetch.mockResolvedValue(json(200, { files: [] }));
    const files = [{ name: 'a.md', size: 3, sha256: null, head: 'YWI=' }];
    expect(await api.uploadPlan('p1', files)).toEqual({ files: [] });
    const [, , path, opts] = apiFetch.mock.calls[0];
    expect(`${opts.method} ${path}`).toBe('POST /v1/projects/p1/uploads/plan');
    expect(JSON.parse(opts.body)).toEqual({ files });
  });

  it('uploadPlan throws the server sentence for too many files and for a full disk', async () => {
    const message = 'At most 200 files can be uploaded at once. Upload them in smaller groups.';
    apiFetch.mockResolvedValue({ ok: false, status: 413, json: async () => ({ detail: { error: 'too_many_files', message } }) });
    await expect(api.uploadPlan('p1', [])).rejects.toThrow(message);
    const full = 'This upload needs 3 MB, and the server has 100 MB free, of which it keeps 1024 MB in reserve.';
    apiFetch.mockResolvedValue({ ok: false, status: 507, json: async () => ({ detail: { error: 'not_enough_space', message: full } }) });
    await expect(api.uploadPlan('p1', [])).rejects.toThrow(full);
  });

  it('uploadFile sends on_match, on_older and document_id, and passes progress through', async () => {
    apiUpload.mockResolvedValue({ ok: true, status: 201, body: { result: 'version' } });
    const file = new File(['x'], 'a.md');
    const onProgress = vi.fn();
    const out = await api.uploadFile('p1', file, { on_match: 'version', on_older: 'leave', document_id: 'd9' }, { onProgress });
    expect(out).toEqual({ result: 'version' });
    const [, , path, form, opts] = apiUpload.mock.calls[0];
    expect(path).toBe('/v1/projects/p1/uploads');
    expect(form.get('file').name).toBe('a.md');
    expect([form.get('on_match'), form.get('on_older'), form.get('document_id')]).toEqual(['version', 'leave', 'd9']);
    expect(opts.onProgress).toBe(onProgress);
  });

  it('uploadFile leaves document_id out when there is none', async () => {
    apiUpload.mockResolvedValue({ ok: true, status: 201, body: {} });
    await api.uploadFile('p1', new File(['x'], 'a.md'), { on_match: 'separate', on_older: 'leave' });
    expect(apiUpload.mock.calls[0][3].has('document_id')).toBe(false);
  });

  it('uploadFile throws the server sentence with status and code', async () => {
    apiUpload.mockResolvedValue({ ok: false, status: 403, body: { detail: { error: 'insufficient_role', required: 'contributor' } } });
    const err = await api.uploadFile('p1', new File(['x'], 'a.md'), {}).catch((e) => e);
    expect(err.message).toBe('Only Contributors, Maintainers or Owners can do that.');
    expect([err.status, err.code]).toEqual([403, 'insufficient_role']);
  });

  it('uploadFile reports a network failure plainly', async () => {
    apiUpload.mockResolvedValue({ ok: false, status: 0, body: null });
    const err = await api.uploadFile('p1', new File(['x'], 'a.md'), {}).catch((e) => e);
    expect(err.message).toBe('The upload was cut off. Check the connection and try again.');
    expect(err.status).toBe(0);
  });
});
