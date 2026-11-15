import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useBulkUpload } from './useBulkUpload';
import { forgetBatches } from '../lib/bulkUploadStore';

const item = (path, data = path) => ({ file: new File([data], path.split('/').pop()), path });
const planRow = (name, action = 'new', over = {}) => ({ name, action, document: null, version: null, candidates: [],
  code: null, message: null, ...over });
const doc = { id: 'd1', name: 'setup.pdf', version: 2 };

// uploadFile calls wait until the test settles them, so the test controls what is in flight.
function fakeApi(planFiles) {
  const pending = [];
  const api = {
    uploadPlan: vi.fn(async (_pid, files) => ({ files: planFiles ?? files.map((f) => planRow(f.name)) })),
    uploadFile: vi.fn((_pid, file, fields, opts) => new Promise((resolve, reject) => {
      pending.push({ file, fields, opts, resolve, reject });
    })),
  };
  return { api, pending };
}

async function planned(api, items, onLanded = vi.fn()) {
  const hook = renderHook(() => useBulkUpload({ api, projectId: 'p1', onLanded }));
  await act(async () => { await hook.result.current.pick(items); });
  return hook;
}

const flush = () => act(async () => { await Promise.resolve(); });

describe('useBulkUpload', () => {
  beforeEach(() => forgetBatches());
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('reads, plans and gives every row its default with nothing touched', async () => {
    const { api } = fakeApi([planRow('a.md'), planRow('setup.pdf', 'version', { document: doc, version: 3 }),
      planRow('old.pdf', 'older_version', { document: doc, version: 1 })]);
    const { result } = await planned(api, [item('h/setup.pdf', '%PDF-1'), item('h/a.md', 'a.md'), item('h/old.pdf', '%PDF-2')]);
    expect(result.current.phase).toBe('planned');
    const sent = api.uploadPlan.mock.calls[0][1];
    expect(sent.map((f) => f.name)).toEqual(['a.md', 'old.pdf', 'setup.pdf']); // path order
    expect(sent[0]).toEqual({ name: 'a.md', size: 4, sha256: expect.stringMatching(/^[0-9a-f]{64}$/), head: 'YS5tZA==' });
    expect(result.current.rows.map((r) => r.choice)).toEqual([null, 'version', 'leave']);
    expect(result.current.hashed).toBe(true);
  });

  it('counts left-out files without sending them to the plan', async () => {
    const { api } = fakeApi();
    const { result } = await planned(api, [item('a.md'), item('b.png'), item('.git/x')]);
    expect(result.current.leftOut).toBe(1);
    expect(api.uploadPlan.mock.calls[0][1]).toHaveLength(1);
  });

  it('skips the later repeated name by default', async () => {
    const { api } = fakeApi([planRow('README.md'), planRow('README.md')]);
    const { result } = await planned(api, [item('a/README.md'), item('b/README.md')]);
    expect(result.current.rows.map((r) => [r.path, !!r.repeat, r.choice])).toEqual([
      ['a/README.md', false, null], ['b/README.md', true, 'skip']]);
  });

  it('sends each per-file choice in the request', async () => {
    const { api, pending } = fakeApi([planRow('setup.pdf', 'version', { document: doc, version: 3 })]);
    const { result } = await planned(api, [item('setup.pdf', '%PDF-1')]);
    act(() => result.current.choose(0, 'separate'));
    act(() => result.current.start());
    expect(pending[0].fields).toEqual({ on_match: 'separate', on_older: 'leave' });
  });

  it('has at most two files in flight', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, ['a', 'b', 'c', 'd', 'e'].map((n) => item(`${n}.md`)));
    act(() => result.current.start());
    expect(pending).toHaveLength(2);
    await act(async () => { pending[0].resolve({ result: 'created' }); });
    expect(pending).toHaveLength(3);
    expect(result.current.rows.filter((r) => r.status === 'sending')).toHaveLength(2);
  });

  it('Stop lets the two in flight finish and starts no more', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, ['a', 'b', 'c', 'd'].map((n) => item(`${n}.md`)));
    act(() => result.current.start());
    act(() => result.current.stop());
    expect(result.current.rows.map((r) => r.status)).toEqual(['sending', 'sending', 'stopped', 'stopped']);
    await act(async () => { pending[0].resolve({ result: 'created' }); pending[1].resolve({ result: 'created' }); });
    expect(pending).toHaveLength(2);
    expect(result.current.phase).toBe('done');
    expect(result.current.summary).toMatchObject({ sent: 2, created: 2, stopped: 2, failed: 0 });
  });

  it('keeps a failed row with the server sentence, and Try again re-sends only that row', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, [item('a.md'), item('b.md')]);
    act(() => result.current.start());
    await act(async () => {
      pending[0].reject(Object.assign(new Error('Unsupported file type.'), { status: 415, code: 'unsupported_type' }));
      pending[1].resolve({ result: 'created' });
    });
    expect(result.current.rows[0]).toMatchObject({ status: 'failed', error: 'Unsupported file type.' });
    expect(result.current.phase).toBe('done');
    act(() => result.current.retry(0));
    expect(pending).toHaveLength(3);
    expect(pending[2].file.name).toBe('a.md');
    await act(async () => { pending[2].resolve({ result: 'created' }); });
    expect(result.current.summary).toMatchObject({ sent: 2, failed: 0 });
  });

  it('a lost role stops the batch once and leaves the rest stopped', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, ['a', 'b', 'c', 'd', 'e'].map((n) => item(`${n}.md`)));
    act(() => result.current.start());
    const lost = () => Object.assign(new Error('Only Contributors…'), { status: 403, code: 'insufficient_role' });
    await act(async () => { pending[0].reject(lost()); });
    expect(result.current.error).toBe('You can no longer add documents to this project.');
    await act(async () => { pending[1].reject(lost()); });
    expect(pending).toHaveLength(2);
    expect(result.current.rows.map((r) => r.status)).toEqual(['stopped', 'stopped', 'stopped', 'stopped', 'stopped']);
    expect(result.current.phase).toBe('done');
  });

  it('a 401 stops the batch with the signed-out sentence', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, [item('a.md'), item('b.md'), item('c.md')]);
    act(() => result.current.start());
    await act(async () => { pending[0].reject(Object.assign(new Error('x'), { status: 401 })); });
    expect(result.current.error).toBe('You were signed out. Sign in, then drop the files again: what was sent is safe.');
  });

  it('a missing document named by document_id fails that row only', async () => {
    const { api, pending } = fakeApi([planRow('README.md', 'ambiguous', { candidates: [
      { id: 'c1', version: 1, added_by: null, added_at: null }] }), planRow('b.md')]);
    const { result } = await planned(api, [item('README.md'), item('b.md')]);
    act(() => result.current.choose(0, 'doc:c1'));
    act(() => result.current.start());
    await act(async () => { pending[0].reject(Object.assign(new Error('gone'), { status: 404, code: 'not_found' })); });
    expect(result.current.error).toBe(null);
    expect(result.current.rows[0].status).toBe('failed');
    expect(pending).toHaveLength(2);
  });

  it("row shows the server's result, not the plan's", async () => {
    const { api, pending } = fakeApi([planRow('report.pdf')]);
    const { result } = await planned(api, [item('report.pdf', '%PDF-1')]);
    act(() => result.current.start());
    await act(async () => { pending[0].resolve({ result: 'version', document: { id: 'x', name: 'report.pdf', version: 2 } }); });
    expect(result.current.rows[0].answer.result).toBe('version');
    expect(result.current.summary).toMatchObject({ sent: 1, created: 0, versions: 1 });
  });

  it('shows progress while a file is sent', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, [item('a.md')]);
    act(() => result.current.start());
    act(() => pending[0].opts.onProgress(64));
    expect(result.current.rows[0].percent).toBe(64);
  });

  it('re-reads the list at most once a second while files land, and once at the end', async () => {
    const onLanded = vi.fn();
    const { api, pending } = fakeApi();
    const { result } = await planned(api, ['a', 'b', 'c', 'd'].map((n) => item(`${n}.md`)), onLanded);
    vi.useFakeTimers();
    act(() => result.current.start());
    await act(async () => { pending[0].resolve({ result: 'created' }); pending[1].resolve({ result: 'created' }); });
    await act(async () => { pending[2].resolve({ result: 'created' }); });
    expect(onLanded).toHaveBeenCalledTimes(0);
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(onLanded).toHaveBeenCalledTimes(1);
    await act(async () => { pending[3].resolve({ result: 'created' }); });
    expect(onLanded).toHaveBeenCalledTimes(2); // the end: at once, not after the second
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(onLanded).toHaveBeenCalledTimes(2);
  });

  it('without hashing, says so and sends no hash', async () => {
    vi.stubGlobal('crypto', {});
    const { api } = fakeApi();
    const { result } = await planned(api, [item('a.md')]);
    expect(result.current.hashed).toBe(false);
    expect(api.uploadPlan.mock.calls[0][1][0].sha256).toBe(null);
  });

  it('a refused plan keeps its sentence and nothing is sent', async () => {
    const api = { uploadPlan: vi.fn(async () => { throw new Error('At most 2 files.'); }), uploadFile: vi.fn() };
    const { result } = await planned(api, [item('a.md'), item('b.md'), item('c.md')]);
    expect(result.current).toMatchObject({ phase: 'idle', error: 'At most 2 files.' });
    expect(api.uploadFile).not.toHaveBeenCalled();
  });

  it('survives the tab being closed and opened again', async () => {
    const { api, pending } = fakeApi();
    const first = await planned(api, [item('a.md'), item('b.md'), item('c.md')]);
    act(() => first.result.current.start());
    first.unmount();
    await act(async () => { pending[0].resolve({ result: 'created' }); });
    const again = renderHook(() => useBulkUpload({ api, projectId: 'p1', onLanded: vi.fn() }));
    expect(again.result.current.phase).toBe('sending');
    expect(again.result.current.rows.map((r) => r.status)).toEqual(['done', 'sending', 'sending']);
    await flush();
  });

  it('asks before the page is left while files are in flight', async () => {
    const { api, pending } = fakeApi();
    const { result } = await planned(api, [item('a.md')]);
    act(() => result.current.start());
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    await act(async () => { pending[0].resolve({ result: 'created' }); });
    const later = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(later);
    expect(later.defaultPrevented).toBe(false);
  });

  it('Cancel and Dismiss go back to the start', async () => {
    const { api } = fakeApi();
    const { result } = await planned(api, [item('a.md')]);
    act(() => result.current.reset());
    expect(result.current).toMatchObject({ phase: 'idle', rows: [], error: null });
  });

  it('waits for nothing when no file can be uploaded', async () => {
    const { api } = fakeApi();
    const { result } = await planned(api, [item('pic.png')]);
    expect(result.current).toMatchObject({ phase: 'planned', rows: [], leftOut: 1 });
    expect(api.uploadPlan).not.toHaveBeenCalled();
    await waitFor(() => expect(result.current.phase).toBe('planned'));
  });
});
