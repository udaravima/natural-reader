import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import { UsageSection } from './UsageSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const sums = (over = {}) => ({
  prompt_tokens: 600, eval_tokens: 400, tokens: 1000, requests: 5,
  chat_tokens: 700, reader_tokens: 300, estimated_tokens: 0, ...over,
});
const MODELS = [
  { model: 'ollama:gemma4:e4b', tag: 'local', ...sums({ tokens: 1500, chat_tokens: 1000, reader_tokens: 500, requests: 12 }) },
  { model: 'openrouter:some/model', tag: 'cloud', ...sums({ tokens: 900, estimated_tokens: 108 }) },
  { model: 'gone:old', tag: 'unknown', ...sums({ tokens: 4000, estimated_tokens: 3 }) },
];
const MODELS_WITH_UNKNOWN = [...MODELS, { model: '', tag: 'unknown', ...sums({ tokens: 50, chat_tokens: 0, reader_tokens: 0 }) }];
const USERS = [
  { user_id: 'u1', email: 'ada@example.com', ...sums({ tokens: 2000 }) },
  { user_id: 'u2', email: 'bob@example.com', ...sums({ tokens: 300 }) },
];
const DAYS = [
  { day: '2026-10-25', ...sums({ tokens: 800 }) },
  { day: '2026-10-24', ...sums({ tokens: 200 }) },
];
const ADA_MODELS = [{ model: 'ollama:gemma4:e4b', tag: 'local', ...sums({ tokens: 1234, requests: 4 }) }];

// Answers keyed by the group param; `over` swaps in other answers per test.
function mount(over = {}, props = {}) {
  const showToast = vi.fn();
  apiFetch.mockImplementation(async (h, p, path) => {
    const q = new URL(path, 'http://x').searchParams;
    if (over.handler) {
      const r = over.handler(path, q);
      if (r) return r;
    }
    const g = q.get('group');
    if (g === 'model' && q.has('user_id')) return json(200, over.person ?? ADA_MODELS);
    if (g === 'model') return json(200, over.models ?? MODELS);
    if (g === 'user') return json(200, over.users ?? USERS);
    if (g === 'day') return json(200, over.days ?? DAYS);
    return json(404, {});
  });
  render(<UsageSection theme={theme} apiHost="" apiPort="" showToast={showToast} {...props} />);
  return { showToast };
}

const paths = () => apiFetch.mock.calls.map((c) => c[2]);
const toggle = (name) => screen.getByRole('button', { name });
const cellsOf = (row) => within(row).getAllByRole('cell').map((c) => c.textContent);

describe('UsageSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('defaults to By model: requests group=model and renders the Where tags', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    expect(paths()).toEqual(['/v1/admin/inference/usage?days=7&group=model']);
    expect(toggle('By model').getAttribute('aria-pressed')).toBe('true');
    const rows = screen.getAllByRole('row');
    expect(cellsOf(rows[1])).toEqual(['ollama:gemma4:e4b', 'Local', '1,000', '500', '12', '1,500', '—']);
    expect(cellsOf(rows[2])[1]).toBe('Cloud');
    expect(cellsOf(rows[3])[1]).toBe('Unknown');
    expect([...rows[0].querySelectorAll('th')].map((h) => h.textContent))
      .toEqual(['Model', 'Where', 'Chat', 'Reader', 'Requests', 'Total', 'Estimated']);
    // No model filter in the By model view.
    expect(screen.queryByLabelText('Model')).toBeNull();
  });

  it('shows a model-less row as Unknown, and total is not chat + reader', async () => {
    mount({ models: MODELS_WITH_UNKNOWN });
    await screen.findByText('ollama:gemma4:e4b');
    const unknown = screen.getAllByRole('row')[4];
    expect(cellsOf(unknown)).toEqual(['Unknown', 'Unknown', '0', '0', '5', '50', '—']);
  });

  it('wraps tables in overflow-x-auto', async () => {
    mount();
    const table = (await screen.findAllByRole('table'))[0];
    expect(table.parentElement.classList.contains('overflow-x-auto')).toBe(true);
  });

  it('By person requests group=user and aria-pressed follows the toggle', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    expect(paths()).toContain('/v1/admin/inference/usage?days=7&group=user');
    expect(toggle('By person').getAttribute('aria-pressed')).toBe('true');
    expect(toggle('By model').getAttribute('aria-pressed')).toBe('false');
    expect(toggle('By day').getAttribute('aria-pressed')).toBe('false');
    const head = [...screen.getAllByRole('row')[0].querySelectorAll('th')].map((h) => h.textContent);
    expect(head.slice(0, 6)).toEqual(['Person', 'Chat', 'Reader', 'Requests', 'Total', 'Estimated']);
  });

  it('By day requests group=day and renders the day as given', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By day'));
    await screen.findByText('2026-10-25');
    expect(paths()).toContain('/v1/admin/inference/usage?days=7&group=day');
    expect(cellsOf(screen.getAllByRole('row')[1])).toEqual(['2026-10-25', '700', '300', '5', '800', '—']);
  });

  it('expanding a person fetches group=model&user_id once and sets aria-expanded', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    const btn = toggle('Models for ada@example.com');
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    await screen.findByText('1,234');
    const mine = '/v1/admin/inference/usage?days=7&group=model&user_id=u1';
    expect(paths().filter((x) => x === mine)).toHaveLength(1);
    // Collapse and reopen: no second request.
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('1,234')).toBeNull();
    fireEvent.click(btn);
    await screen.findByText('1,234');
    expect(paths().filter((x) => x === mine)).toHaveLength(1);
  });

  it('changing the range clears cached expansions, so a reopen fetches again', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    fireEvent.click(toggle('Models for ada@example.com'));
    await screen.findByText('1,234');
    fireEvent.change(screen.getByLabelText('Usage days'), { target: { value: '30' } });
    await waitFor(() => expect(paths()).toContain('/v1/admin/inference/usage?days=30&group=user'));
    await screen.findByText('ada@example.com');
    expect(toggle('Models for ada@example.com').getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle('Models for ada@example.com'));
    await screen.findByText('1,234');
    expect(paths()).toContain('/v1/admin/inference/usage?days=30&group=model&user_id=u1');
  });

  it('the Model filter lists every model, sends no model param for All, and encodes a pick', async () => {
    mount({ models: MODELS_WITH_UNKNOWN });
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    const select = screen.getByLabelText('Model');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'All models', 'ollama:gemma4:e4b', 'openrouter:some/model', 'gone:old', 'Unknown',
    ]);
    expect(paths().filter((x) => x.includes('group=user'))).toEqual(['/v1/admin/inference/usage?days=7&group=user']);

    fireEvent.change(select, { target: { value: [...select.options].find((o) => o.textContent === 'ollama:gemma4:e4b').value } });
    await waitFor(() => expect(paths()).toContain('/v1/admin/inference/usage?days=7&group=user&model=ollama%3Agemma4%3Ae4b'));

    fireEvent.change(select, { target: { value: [...select.options].find((o) => o.textContent === 'Unknown').value } });
    await waitFor(() => expect(paths()).toContain('/v1/admin/inference/usage?days=7&group=user&model='));

    fireEvent.change(select, { target: { value: select.options[0].value } });
    await waitFor(() => expect(paths().filter((x) => x === '/v1/admin/inference/usage?days=7&group=user')).toHaveLength(2));
  });

  it('the Model filter also applies to By day, and a person expansion fetches without it', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By day'));
    await screen.findByText('2026-10-25');
    const select = screen.getByLabelText('Model');
    fireEvent.change(select, { target: { value: [...select.options].find((o) => o.textContent === 'openrouter:some/model').value } });
    await waitFor(() => expect(paths()).toContain('/v1/admin/inference/usage?days=7&group=day&model=openrouter%3Asome%2Fmodel'));
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    fireEvent.click(toggle('Models for ada@example.com'));
    await waitFor(() => expect(paths()).toContain('/v1/admin/inference/usage?days=7&group=model&user_id=u1'));
  });

  it('a person\'s expansion shows only the rows of the selected Model filter, without a new request', async () => {
    const person = [
      { model: 'ollama:gemma4:e4b', tag: 'local', ...sums({ tokens: 1111 }) },
      { model: 'openrouter:some/model', tag: 'cloud', ...sums({ tokens: 2222 }) },
      { model: '', tag: 'unknown', ...sums({ tokens: 3333 }) },
    ];
    mount({ models: MODELS_WITH_UNKNOWN, person });
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    const select = screen.getByLabelText('Model');
    const option = (label) => [...select.options].find((o) => o.textContent === label).value;
    fireEvent.click(toggle('Models for ada@example.com'));
    await screen.findByText('1,111');
    expect(screen.getByText('2,222')).toBeTruthy();   // All models: all three rows
    expect(screen.getByText('3,333')).toBeTruthy();
    const mine = '/v1/admin/inference/usage?days=7&group=model&user_id=u1';

    fireEvent.change(select, { target: { value: option('openrouter:some/model') } });
    await screen.findByText('2,222');
    expect(screen.queryByText('1,111')).toBeNull();
    expect(screen.queryByText('3,333')).toBeNull();

    fireEvent.change(select, { target: { value: option('Unknown') } });
    await screen.findByText('3,333');
    expect(screen.queryByText('2,222')).toBeNull();

    fireEvent.change(select, { target: { value: select.options[0].value } });
    await screen.findByText('1,111');
    expect(screen.getByText('2,222')).toBeTruthy();
    // The cache serves every filter: one request for the person.
    expect(paths().filter((x) => x === mine)).toHaveLength(1);
  });

  it('the Unknown note appears only when the By model data has a model-less row, in any view', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    expect(screen.queryByText(/no model and shows as Unknown/)).toBeNull();
    expect(screen.getByText(/Each day is counted in the deployment/)).toBeTruthy();
  });

  it('shows the Unknown note in By model and in By person when a model-less row exists', async () => {
    mount({ models: MODELS_WITH_UNKNOWN });
    const note = 'Usage recorded before this version has no model and shows as Unknown.';
    await screen.findByText(note);
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    expect(screen.getByText(note)).toBeTruthy();
    fireEvent.click(toggle('By day'));
    await screen.findByText('2026-10-25');
    expect(screen.getByText(note)).toBeTruthy();
  });

  it('Estimated renders a whole percent, <1% and a dash; plus the explanation note', async () => {
    mount({ models: [
      { model: 'a:one', tag: 'cloud', ...sums({ tokens: 1000, estimated_tokens: 120 }) },
      { model: 'a:two', tag: 'cloud', ...sums({ tokens: 1000, estimated_tokens: 1 }) },
      { model: 'a:three', tag: 'cloud', ...sums({ tokens: 1000, estimated_tokens: 0 }) },
      { model: 'a:four', tag: 'cloud', ...sums({ tokens: 0, estimated_tokens: 0 }) },
    ] });
    await screen.findByText('a:one');
    const rows = screen.getAllByRole('row');
    expect(cellsOf(rows[1]).at(-1)).toBe('12%');
    expect(cellsOf(rows[2]).at(-1)).toBe('<1%');
    expect(cellsOf(rows[3]).at(-1)).toBe('—');
    expect(cellsOf(rows[4]).at(-1)).toBe('—');
    expect(screen.getByText('Estimated: counted from text length because the provider sent no token counts.')).toBeTruthy();
  });

  it('changing the range refetches the current view', async () => {
    mount();
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By day'));
    await screen.findByText('2026-10-25');
    fireEvent.change(screen.getByLabelText('Usage days'), { target: { value: '30' } });
    await waitFor(() => expect(paths()).toContain('/v1/admin/inference/usage?days=30&group=day'));
    expect(paths()).toContain('/v1/admin/inference/usage?days=30&group=model');
  });

  it('shows the empty message and no notes when there is no usage', async () => {
    mount({ models: [] });
    await screen.findByText('No usage recorded yet.');
    expect(screen.queryByText(/Each day is counted/)).toBeNull();
    expect(screen.queryByText(/^Estimated:/)).toBeNull();
  });

  it('keeps the load-failure toast copy', async () => {
    const { showToast } = mount({ handler: () => json(500, {}) });
    await screen.findByText('No usage recorded yet.');
    expect(showToast).toHaveBeenCalledWith('Could not load usage: HTTP 500', 5000);
  });

  it('drops an out-of-order answer: a slow reply for an old range never overwrites the new one', async () => {
    let releaseOld;
    const old = new Promise((r) => { releaseOld = r; });
    mount({ handler: (path) => {
      if (path.includes('days=7&group=model')) return old.then(() => json(200, [{ model: 'slow:old', tag: 'local', ...sums() }]));
      if (path.includes('days=30&group=model')) return json(200, [{ model: 'fast:new', tag: 'local', ...sums() }]);
      return null;
    } });
    fireEvent.change(screen.getByLabelText('Usage days'), { target: { value: '30' } });
    await screen.findByText('fast:new');
    await act(async () => { releaseOld(); await new Promise((r) => setTimeout(r, 0)); });
    // Without the sequence guard the stale answer lands and the table falls back to Loading…
    expect(screen.queryByText('slow:old')).toBeNull();
    expect(screen.getByText('fast:new')).toBeTruthy();
  });

  it('drops an out-of-order view answer: a slow By person reply never overwrites the filtered one', async () => {
    let releaseSlow;
    const slow = new Promise((r) => { releaseSlow = r; });
    mount({ handler: (path) => {
      if (path.endsWith('group=user')) return slow.then(() => json(200, [{ user_id: 'u9', email: 'slow@example.com', ...sums() }]));
      if (path.includes('group=user&model=')) return json(200, [{ user_id: 'u1', email: 'fast@example.com', ...sums() }]);
      return null;
    } });
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    const select = await screen.findByLabelText('Model');
    fireEvent.change(select, { target: { value: [...select.options].find((o) => o.textContent === 'gone:old').value } });
    await screen.findByText('fast@example.com');
    await act(async () => { releaseSlow(); await new Promise((r) => setTimeout(r, 0)); });
    expect(screen.queryByText('slow@example.com')).toBeNull();
    expect(screen.getByText('fast@example.com')).toBeTruthy();
  });

  it('drops an expansion answered after the range changed: reopening fetches the new range', async () => {
    let releaseOld;
    const old = new Promise((r) => { releaseOld = r; });
    mount({ handler: (path) => {
      if (path.includes('days=7&group=model&user_id=u1')) return old.then(() => json(200, [{ model: 'stale:7d', tag: 'local', ...sums() }]));
      if (path.includes('days=30&group=model&user_id=u1')) return json(200, [{ model: 'fresh:30d', tag: 'local', ...sums() }]);
      return null;
    } });
    await screen.findByText('ollama:gemma4:e4b');
    fireEvent.click(toggle('By person'));
    await screen.findByText('ada@example.com');
    fireEvent.click(toggle('Models for ada@example.com'));          // 7-day answer now pending
    fireEvent.change(screen.getByLabelText('Usage days'), { target: { value: '30' } });
    await screen.findByText('ada@example.com');
    await act(async () => { releaseOld(); await new Promise((r) => setTimeout(r, 0)); });
    // Without the epoch guard the stale rows are cached under the person, and reopening
    // shows them instead of fetching the 30-day rows.
    fireEvent.click(toggle('Models for ada@example.com'));
    await screen.findByText('fresh:30d');
    expect(screen.queryByText('stale:7d')).toBeNull();
    expect(paths()).toContain('/v1/admin/inference/usage?days=30&group=model&user_id=u1');
  });
});
