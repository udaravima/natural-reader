import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { useSectionSettings } from './useSectionSettings';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const row = (over = {}) => ({ value: 'UTC', source: 'default', env: 'TZ', fallback: 'UTC', fallback_source: 'default', ...over });
const view = (settings, info = []) => ({ settings, info });

// GET answers `getBody`; a PUT answers `put(body)` (default: echo the view back unchanged).
function mockServer(getBody, put) {
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (opts?.method === 'PUT') return put ? put(JSON.parse(opts.body)) : json(200, getBody);
    return json(200, getBody);
  });
}
const puts = () => apiFetch.mock.calls.filter((c) => c[3]?.method === 'PUT').map((c) => JSON.parse(c[3].body));

async function ready(getBody, put) {
  mockServer(getBody, put);
  const hook = renderHook(() => useSectionSettings('', '', 'deployment'));
  await waitFor(() => expect(hook.result.current.status).toBe('ready'));
  return hook;
}

describe('useSectionSettings', () => {
  beforeEach(() => vi.clearAllMocks());

  it('loads the section and reads values, sources, the env name and the fallback', async () => {
    const info = [{ label: 'Version', value: '1.0' }];
    const { result } = await ready(view({ timezone: row({ value: 'Europe/Berlin', source: 'admin' }) }, info));
    expect(apiFetch.mock.calls[0].slice(0, 3)).toEqual(['', '', '/v1/admin/settings/deployment']);
    expect(result.current.info).toEqual(info);
    expect([result.current.value('timezone'), result.current.source('timezone')]).toEqual(['Europe/Berlin', 'admin']);
    expect([result.current.envName('timezone'), result.current.fallback('timezone')]).toEqual(['TZ', 'UTC']);
    expect(result.current.dirty).toBe(false);
  });

  it('drafts, resets to the fallback, and saves only what changed', async () => {
    const { result } = await ready(view({
      timezone: row({ value: 'Europe/Berlin', source: 'admin' }),
      other: row({ value: 5, source: 'env', fallback: 5, fallback_source: 'env' }),
    }));
    act(() => result.current.useServer('timezone'));
    expect([result.current.value('timezone'), result.current.source('timezone'), result.current.dirty]).toEqual(['UTC', 'default', true]);
    expect(result.current.canUseServer('timezone')).toBe(false);
    let saved;
    await act(async () => { saved = await result.current.save(); });
    expect(saved).toBe(true);
    expect(puts()).toEqual([{ changes: { timezone: null } }]);
    expect(result.current.dirty).toBe(false);
  });

  it('a drafted value shows as set here and sends only that name', async () => {
    const { result } = await ready(view({ timezone: row(), other: row({ value: 5 }) }));
    expect(result.current.canUseServer('timezone')).toBe(false);
    act(() => result.current.set('timezone', 'Asia/Tokyo'));
    expect([result.current.value('timezone'), result.current.source('timezone'), result.current.canUseServer('timezone')])
      .toEqual(['Asia/Tokyo', 'admin', true]);
    expect(result.current.dirty).toBe(true);
    await act(async () => { await result.current.save(); });
    expect(puts()).toEqual([{ changes: { timezone: 'Asia/Tokyo' } }]);
  });

  it('adopts the response of a save', async () => {
    const after = view({ timezone: row({ value: 'Asia/Tokyo', source: 'admin' }) }, [{ label: 'New', value: 'x' }]);
    const { result } = await ready(view({ timezone: row() }), () => json(200, after));
    act(() => result.current.set('timezone', 'Asia/Tokyo'));
    await act(async () => { await result.current.save(); });
    expect([result.current.value('timezone'), result.current.source('timezone'), result.current.dirty]).toEqual(['Asia/Tokyo', 'admin', false]);
    expect(result.current.info).toEqual([{ label: 'New', value: 'x' }]);
    expect(result.current.saving).toBe(false);
  });

  it('setting a value back to what it was clears that draft', async () => {
    const { result } = await ready(view({ limit: row({ value: [1, 2], source: 'admin' }) }));
    act(() => result.current.set('limit', [1]));
    expect(result.current.dirty).toBe(true);
    act(() => result.current.set('limit', [1, 2]));
    expect(result.current.dirty).toBe(false);
  });

  it('a value equal to one the server merely supplies is still a choice', async () => {
    const { result } = await ready(view({ timezone: row() }));
    act(() => result.current.set('timezone', 'UTC'));
    expect(result.current.dirty).toBe(true);
    expect(result.current.source('timezone')).toBe('admin');
  });

  it('using the server setting on a row the console never set is nothing to save', async () => {
    const { result } = await ready(view({ timezone: row() }));
    act(() => result.current.useServer('timezone'));
    expect(result.current.dirty).toBe(false);
  });

  it('keeps edits made while a save is out, and drops only what was saved unchanged', async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const after = view({ a: row({ value: 1, source: 'admin' }), b: row(), c: row({ value: 'x', source: 'admin' }) });
    const { result } = await ready(
      view({ a: row({ value: 0 }), b: row(), c: row({ value: 'x', source: 'admin' }) }),
      async () => { await gate; return json(200, after); },
    );
    act(() => { result.current.set('a', 1); result.current.useServer('c'); });
    let saved;
    act(() => { saved = result.current.save(); });
    act(() => { result.current.set('a', 2); result.current.set('b', 'new'); });   // while the PUT is out
    await act(async () => { release(); await saved; });
    expect(puts()).toEqual([{ changes: { a: 1, c: null } }]);
    expect(result.current.dirty).toBe(true);
    expect([result.current.value('a'), result.current.value('b')]).toEqual([2, 'new']);
    expect(result.current.source('a')).toBe('admin');
    // c was saved as sent and is no longer drafted: it reads from the adopted view again
    expect(result.current.value('c')).toBe('x');
  });

  it('two save() calls in the same tick send one request', async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const { result } = await ready(view({ a: row() }), async () => { await gate; return json(200, view({ a: row({ value: 1, source: 'admin' }) })); });
    act(() => result.current.set('a', 1));
    let first;
    let second;
    act(() => { first = result.current.save(); second = result.current.save(); });
    await act(async () => { release(); });
    expect(await second).toBe(false);
    expect(await first).toBe(true);
    expect(puts()).toHaveLength(1);
  });

  it('discard drops the drafts without a request', async () => {
    const { result } = await ready(view({ timezone: row() }));
    act(() => result.current.set('timezone', 'Asia/Tokyo'));
    act(() => result.current.discard());
    expect([result.current.value('timezone'), result.current.dirty]).toEqual(['UTC', false]);
    expect(puts()).toEqual([]);
  });

  it('keeps the draft and shows the server message when a save is refused', async () => {
    const { result } = await ready(
      view({ timezone: row() }),
      () => json(422, { detail: { error: 'invalid_setting', message: 'Nope.', setting: 'timezone' } }),
    );
    act(() => result.current.set('timezone', 'Mars/Base'));
    let saved;
    await act(async () => { saved = await result.current.save(); });
    expect(saved).toBe(false);
    expect(result.current.error).toBe('Nope.');
    expect(result.current.dirty).toBe(true);
    expect(result.current.value('timezone')).toBe('Mars/Base');
    expect(result.current.saving).toBe(false);
  });

  it('shows a string detail, or the status when there is none', async () => {
    let body = { detail: 'Not allowed.' };
    const { result } = await ready(view({ timezone: row() }), () => json(403, body));
    act(() => result.current.set('timezone', 'Asia/Tokyo'));
    await act(async () => { await result.current.save(); });
    expect(result.current.error).toBe('Not allowed.');
    body = {};
    await act(async () => { await result.current.save(); });
    expect(result.current.error).toBe('HTTP 403');
  });

  it('a later successful save clears the error', async () => {
    let n = 0;
    const after = view({ timezone: row({ value: 'Asia/Tokyo', source: 'admin' }) });
    const { result } = await ready(view({ timezone: row() }), () => (n++ ? json(200, after) : json(500, {})));
    act(() => result.current.set('timezone', 'Asia/Tokyo'));
    await act(async () => { await result.current.save(); });
    expect(result.current.error).toBe('HTTP 500');
    await act(async () => { await result.current.save(); });
    expect(result.current.error).toBeNull();
  });

  it('reports a network failure on save as an error', async () => {
    const { result } = await ready(view({ timezone: row() }), () => { throw new Error('offline'); });
    act(() => result.current.set('timezone', 'Asia/Tokyo'));
    let saved;
    await act(async () => { saved = await result.current.save(); });
    expect([saved, result.current.error, result.current.dirty]).toEqual([false, 'offline', true]);
  });

  it('reports failed when the section cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(404, { detail: { error: 'unknown_section' } }));
    const { result } = renderHook(() => useSectionSettings('', '', 'nope'));
    expect(result.current.status).toBe('loading');
    await waitFor(() => expect(result.current.status).toBe('failed'));
  });

  it('reports failed when the request throws', async () => {
    apiFetch.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useSectionSettings('', '', 'deployment'));
    await waitFor(() => expect(result.current.status).toBe('failed'));
  });
});
