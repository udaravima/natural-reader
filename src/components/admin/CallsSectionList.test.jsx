import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, fireEvent, screen } from '@testing-library/react';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';
import { CallsSection } from './CallsSection';

// The list's rules, against a fake server with real keyset paging: whatever mix of polls, Refresh and More happens,
// the list shown is an unbroken newest-first run of the server's calls.
const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const T0 = Date.UTC(2026, 10, 1);
const call = (n) => ({
  id: `c${n}`, lineId: 'l1', lineName: 'Front desk', startedAt: new Date(T0 + n * 60000).toISOString(),
  endedAt: new Date(T0 + n * 60000 + 30000).toISOString(), lengthS: 30, endReason: 'caller', turns: 1,
  slowestFirstSoundS: 1.0, transcript: true, inProgress: false,
});
const range = (hi, lo) => Array.from({ length: hi - lo + 1 }, (_, i) => call(hi - i));
const ids = (calls) => calls.map((c) => c.id);
const shown = () => [...document.querySelectorAll('[data-call]')].map((e) => e.getAttribute('data-call'));

const server = { all: [], limits: [] };
function page(limit, before) {
  const from = before ? server.all.findIndex((c) => c.id === before.slice('after:'.length)) + 1 : 0;
  const calls = server.all.slice(from, from + Math.min(limit, 200));   // the endpoint caps a page at 200
  return { calls, next: from + calls.length < server.all.length ? `after:${calls.at(-1).id}` : null };
}

let visibility = 'visible';
beforeEach(() => {
  apiFetch.mockReset();
  server.limits = [];
  apiFetch.mockImplementation(async (h, p, path) => {
    if (path.startsWith('/v1/admin/phone/calls?')) {
      const q = new URLSearchParams(path.split('?')[1]);
      server.limits.push(Number(q.get('limit')));
      return json(200, page(Number(q.get('limit')), q.get('before')));
    }
    return json(404, {});
  });
  visibility = 'visible';
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const mount = () => render(<CallsSection theme={theme} apiHost="h" apiPort="1" zone="UTC" showToast={vi.fn()} />);
const wait = async (ms) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const more = async () => { fireEvent.click(screen.getByRole('button', { name: 'More' })); await wait(10); };

describe('CallsSection list', () => {
  it('after a hidden tab misses more than 25 calls, the list is one unbroken run and More continues from its end', async () => {
    server.all = range(40, 1);
    mount();
    await wait(10);
    expect(shown()).toEqual(ids(range(40, 16)));
    visibility = 'hidden';
    document.dispatchEvent(new Event('visibilitychange'));
    server.all = range(70, 1);                         // 30 new calls arrive while the tab is hidden
    await wait(60000);
    visibility = 'visible';
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    await wait(10);
    expect(shown()).toEqual(ids(range(70, 46)));      // the newest 25, replacing the old rows: no hole
    while (screen.queryByRole('button', { name: 'More' })) await more();
    expect(shown()).toEqual(ids(range(70, 1)));       // no hole, no repeat
  });

  it('keeps the pages More loaded across a poll, by reading as many rows as are shown', async () => {
    server.all = range(60, 1);
    mount();
    await wait(10);
    await more();                                      // 50 rows
    expect(shown()).toEqual(ids(range(60, 11)));
    server.all = range(61, 1);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await wait(10);
    expect(server.limits.at(-1)).toBe(50);
    expect(shown()).toEqual(ids(range(61, 12)));      // one new call on top, the same number of rows
    await more();
    expect(shown()).toEqual(ids(range(61, 1)));        // More continues from the last row read
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
  });

  it('drops a call deleted elsewhere on the next read', async () => {
    server.all = range(30, 1);
    mount();
    await wait(10);
    server.all = server.all.filter((c) => c.id !== 'c20');
    await wait(10000);
    expect(shown()).toEqual(ids(range(30, 21)).concat(ids(range(19, 5))));
    expect(shown()).not.toContain('c20');
  });

  it('stops at the newest 200 calls: no More, a note, and a new call never opens a hole', async () => {
    server.all = range(300, 1);
    mount();
    await wait(10);
    while (screen.queryByRole('button', { name: 'More' })) await more();
    expect(shown()).toEqual(ids(range(300, 101)));
    expect(screen.getByText('Showing the newest 200 calls.')).toBeTruthy();
    server.all = range(302, 1);                                   // two new calls
    await wait(10000);
    expect(shown()).toEqual(ids(range(302, 103)));                // still one unbroken run of 200
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(server.limits.at(-1)).toBe(200);
  });

  it('says nothing about a cap when everything fits', async () => {
    server.all = range(30, 1);
    mount();
    await wait(10);
    await more();
    expect(screen.queryByText(/Showing the newest/)).toBeNull();
  });
});
