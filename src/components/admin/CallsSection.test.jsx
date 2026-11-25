import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, fireEvent, act } from '@testing-library/react';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';
import { CallsSection } from './CallsSection';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const row = (n, over = {}) => ({
  id: `call-${n}`, lineId: `line-${n % 2}`, lineName: n % 2 ? 'Front desk' : 'Back desk',
  startedAt: `2026-11-2${n}T14:02:00Z`, endedAt: `2026-11-2${n}T14:04:31Z`, lengthS: 151, endReason: 'caller',
  turns: 2, slowestFirstSoundS: 2.9, transcript: true, inProgress: false, ...over,
});
const transcript = (id, over = {}) => ({
  ...row(1), id, stats: [], messages: [
    { id: 'm1', role: 'assistant', content: 'Hello there.', timestamp: 't', status: 'complete', finishReason: 'stop' },
  ], ...over,
});

let visibility;
function setup({ pages, detail = (id) => transcript(id) }) {
  const state = { pages: pages.map((p) => ({ ...p })), seen: [], detailCalls: 0 };
  apiFetch.mockImplementation(async (h, p, path) => {
    state.seen.push(path);
    if (path === '/v1/admin/settings/deployment') return json(200, { settings: { timezone: { value: 'UTC' } } });
    if (path.startsWith('/v1/admin/phone/calls?')) {
      const before = new URLSearchParams(path.split('?')[1]).get('before');
      const at = before ? Number(before) : 0;
      return json(200, { calls: state.pages[at].calls, next: state.pages[at].next ?? null });
    }
    const m = path.match(/^\/v1\/admin\/phone\/calls\/([^/]+)$/);
    if (m) { state.detailCalls += 1; return json(200, typeof detail === 'function' ? detail(m[1], state) : detail); }
    return json(404, {});
  });
  return state;
}
const listCalls = (state) => state.seen.filter((p) => p.startsWith('/v1/admin/phone/calls?'));
const mountIt = (props = {}) => render(<CallsSection theme={theme} apiHost="h" apiPort="1" showToast={vi.fn()} zone="UTC" {...props} />);

describe('CallsSection', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    visibility = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('lists calls of every line with the line, start, length, reason, turns and slowest first sound', async () => {
    setup({ pages: [{ calls: [row(2), row(1)] }] });
    mountIt();
    const back = (await screen.findByText('Back desk')).closest('[data-call]');
    expect(within(back).getByText('2026-11-22 14:02')).toBeTruthy();
    expect(within(back).getByText('2 m 31 s')).toBeTruthy();
    expect(within(back).getByText('caller hung up')).toBeTruthy();
    expect(within(back).getByText('2 turns')).toBeTruthy();
    expect(within(back).getByText('slowest 2.9 s')).toBeTruthy();
    expect(screen.getByText('Front desk')).toBeTruthy();
    expect(screen.getByText('Timings appear when the call ends.')).toBeTruthy();
    expect(listCalls(apiFetchState())[0]).toBe('/v1/admin/phone/calls?limit=25');
  });

  it('pages with More, which asks for the cursor and then goes away on the last page', async () => {
    const state = setup({ pages: [{ calls: [row(2)], next: '1' }, { calls: [row(1)], next: null }] });
    mountIt();
    await screen.findByText('Back desk');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByText('Front desk');
    expect(listCalls(state)).toContain('/v1/admin/phone/calls?limit=25&before=1');
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(screen.getByText('Back desk')).toBeTruthy();    // the first page stays
  });

  it('opens a transcript with its timings beside the messages', async () => {
    setup({ pages: [{ calls: [row(1)] }], detail: (id) => transcript(id, {
      stats: [{ stopped_at: 6.2, heard_at: 7.0, first_audio_at: 9.0, error: null, user_message_id: 'u1', assistant_message_id: 'a1' }],
      messages: [
        { id: 'u1', role: 'user', content: 'When do you open?', timestamp: 't', status: 'complete', finishReason: null },
        { id: 'a1', role: 'assistant', content: 'At nine.', timestamp: 't', status: 'complete', finishReason: 'stop' }] }) });
    mountIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Transcript' }));
    const rows = (await screen.findAllByTestId('phone-row')).map((r) => r.textContent);
    expect(rows).toEqual(['Caller: When do you open?at 0:06', 'Line: At nine.first sound 2.8 s · heard after 0.8 s']);
  });

  it('shows a busy Refresh, then when it updated', async () => {
    let release;
    const state = setup({ pages: [{ calls: [row(1)] }] });
    mountIt();
    await screen.findByText('Front desk');
    const normal = apiFetch.getMockImplementation();
    apiFetch.mockImplementation((...args) => (args[2].startsWith('/v1/admin/phone/calls?')
      ? new Promise((resolve) => { release = () => resolve(json(200, { calls: [row(1)], next: null })); })
      : normal(...args)));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect((await screen.findByRole('button', { name: 'Refreshing…' })).disabled).toBe(true);
    await act(async () => { release(); });
    expect(screen.getByRole('button', { name: 'Refresh' }).disabled).toBe(false);
    expect(screen.getByText(/^Updated \d\d:\d\d:\d\d$/)).toBeTruthy();
    expect(state.seen.length).toBeGreaterThan(0);
  });

  it('shows a load error, as the line list does', async () => {
    apiFetch.mockImplementation(async () => json(500, { detail: { message: 'The database is down.' } }));
    mountIt();
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load the calls: The database is down.');
  });

  describe('live updates', () => {
    const tick = async (ms) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
    const live = (over = {}) => row(1, { endedAt: null, lengthS: null, endReason: null, inProgress: true, slowestFirstSoundS: null, ...over });

    it('polls about every 2 s while a call is in progress, then slows to about every 10 s once none is', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const state = setup({ pages: [{ calls: [live()] }] });
      mountIt();
      await tick(10);
      expect(listCalls(state)).toHaveLength(1);
      await tick(2000);
      expect(listCalls(state)).toHaveLength(2);
      await tick(2000);
      expect(listCalls(state)).toHaveLength(3);
      state.pages[0].calls = [row(1)];                      // it ended
      await tick(2000);
      expect(listCalls(state)).toHaveLength(4);
      await tick(8000);
      expect(listCalls(state)).toHaveLength(4);             // idle now: not every 2 s
      await tick(2000);
      expect(listCalls(state)).toHaveLength(5);
      expect(screen.getByText('caller hung up')).toBeTruthy();
    });

    it('shows a call that starts while nothing is in progress, with no click', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const state = setup({ pages: [{ calls: [] }] });
      mountIt();
      await tick(10);
      expect(screen.getByText('No calls yet.')).toBeTruthy();
      state.pages[0].calls = [live()];
      await tick(10000);
      expect(screen.getByText('Front desk')).toBeTruthy();
      await tick(2000);                                      // and it is now polled fast
      expect(listCalls(state)).toHaveLength(3);
    });

    it('shows no turn count or slowest first sound for a call in progress', async () => {
      setup({ pages: [{ calls: [live({ turns: 0, slowestFirstSoundS: 1.2 })] }] });
      mountIt();
      const card = (await screen.findByText('Front desk')).closest('[data-call]');
      expect(within(card).getByText('in progress')).toBeTruthy();
      expect(within(card).queryByText(/turn/)).toBeNull();
      expect(within(card).queryByText(/slowest/)).toBeNull();
    });

    it('re-reads an open transcript of a call in progress on each poll, so it grows', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const msgs = (n) => Array.from({ length: n }, (_, i) => ({
        id: `m${i}`, role: i % 2 ? 'user' : 'assistant', content: `line ${i}`, timestamp: 't', status: 'complete', finishReason: null }));
      let n = 1;
      const state = setup({ pages: [{ calls: [live()] }], detail: (id) => transcript(id, { messages: msgs(n) }) });
      mountIt();
      await tick(10);
      fireEvent.click(screen.getByRole('button', { name: 'Transcript' }));
      await tick(10);
      expect(screen.getAllByTestId('phone-row')).toHaveLength(1);
      n = 3;
      await tick(2000);
      expect(screen.getAllByTestId('phone-row')).toHaveLength(3);
      expect(state.detailCalls).toBeGreaterThanOrEqual(2);
    });

    it('does not poll while the tab is hidden, and reads at once when it is shown again', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const state = setup({ pages: [{ calls: [live()] }] });
      mountIt();
      await tick(10);
      visibility = 'hidden';
      document.dispatchEvent(new Event('visibilitychange'));
      await tick(10000);
      expect(listCalls(state)).toHaveLength(1);
      visibility = 'visible';
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(listCalls(state)).toHaveLength(2);
    });

    it('stops polling when the section unmounts', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const state = setup({ pages: [{ calls: [live()] }] });
      const { unmount } = mountIt();
      await tick(10);
      unmount();
      await tick(20000);
      expect(listCalls(state)).toHaveLength(1);
    });
  });
  describe('reads that overlap', () => {
    const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

    it('never lets an older list answer land over a newer one', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const inProgress = row(1, { endedAt: null, lengthS: null, endReason: null, inProgress: true, turns: 0 });
      setup({ pages: [{ calls: [inProgress] }] });
      mountIt();
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      const slow = deferred();
      const fast = deferred();
      const queue = [slow, fast];
      apiFetch.mockImplementation((h, p, path) => (path.startsWith('/v1/admin/phone/calls?')
        ? queue.shift().promise : Promise.resolve(json(404, {}))));
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });   // a poll: the older read, still out
      fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));      // a newer one, answered first
      await act(async () => {});
      await act(async () => {});
      await act(async () => { fast.resolve(json(200, { calls: [row(1, { turns: 3 })], next: null })); });
      expect(screen.getByText('3 turns')).toBeTruthy();
      await act(async () => { slow.resolve(json(200, { calls: [inProgress], next: null })); });
      expect(screen.getByText('3 turns')).toBeTruthy();                      // the old answer was dropped
      expect(screen.queryByText('in progress')).toBeNull();
    });

    it('does not reopen a transcript the admin closed while a poll\'s read of it was out', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const slow = deferred();
      setup({ pages: [{ calls: [row(1, { endedAt: null, lengthS: null, endReason: null, inProgress: true })] }] });
      mountIt();
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      fireEvent.click(screen.getByRole('button', { name: 'Transcript' }));
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(screen.getByTestId('phone-transcript')).toBeTruthy();
      const normal = apiFetch.getMockImplementation();
      apiFetch.mockImplementation((...args) => (/\/calls\/call-1$/.test(args[2]) ? slow.promise : normal(...args)));
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });   // the poll's read of the transcript goes out
      fireEvent.click(screen.getByRole('button', { name: 'Transcript' }));   // the admin closes it
      expect(screen.queryByTestId('phone-transcript')).toBeNull();
      await act(async () => { slow.resolve(json(200, transcript('call-1'))); });
      expect(screen.queryByTestId('phone-transcript')).toBeNull();
    });
  });
});

function apiFetchState() {
  return { seen: apiFetch.mock.calls.map((c) => c[2]) };
}
