import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, cleanup } from '@testing-library/react';
import { EmbeddingPicker } from './EmbeddingPicker';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const NONE = { state: 'none', model: null, filled: 0, total: 0, seconds_left: null, error: null };
const building = (filled, total = 5000, more = {}) => ({ state: 'building', model: 'embeddinggemma-2:740m', filled, total,
  seconds_left: null, error: null, ...more });
const GEMMA_IN_USE = { provider: 'ollama', model: 'embeddinggemma-2:740m', dimensions: null, error: null };
const view = (over = {}) => ({
  current: { provider: 'ollama', model: 'nomic-embed-text', dimensions: null, error: null },
  target: { provider: 'ollama', model: 'nomic-embed-text' },
  source: { provider: 'default', model: 'env' },
  env: { provider: 'EMBEDDING_PROVIDER', model: 'EMBEDDING_MODEL' },
  migration: NONE,
  providers: [{ name: 'ollama', kind: 'ollama' }, { name: 'cloud', kind: 'openai' }],
  ...over,
});
const MODELS = ['nomic-embed-text:latest', 'embeddinggemma-2:740m', 'gemma4:e4b'];
const ADMITTED = { ok: true, native: 768, dimensions: null, reason: 'embeddinggemma-2:740m gives 768 numbers, the size the library holds.' };
const SHORTENED = { ok: true, native: 1024, dimensions: 768, reason: 'qwen3-embedding:0.6b gives 1024 numbers and is trained to be shortened: it will be asked for 768.' };
const REFUSED = { ok: false, native: 1024, dimensions: null, reason: 'mxbai-embed-large gives 1024 numbers and the library holds 768: it isn’t in a family trained to be shortened.' };

function mount({ views = [view()], probe = ADMITTED, put, models = MODELS } = {}) {
  let n = 0;
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path.startsWith('/v1/admin/embeddings/models')) return json(200, { models: path.includes('cloud') ? [] : models });
    if (path === '/v1/admin/embeddings/probe') return json(200, probe);
    if (path === '/v1/admin/embeddings' && opts?.method === 'PUT') return put ? put(opts) : json(200, views[views.length - 1]);
    if (path === '/v1/admin/embeddings/migration') return json(200, view());
    if (path === '/v1/admin/embeddings') return json(200, views[Math.min(n++, views.length - 1)]);
    return json(404, {});
  });
  const showToast = vi.fn();
  render(<EmbeddingPicker theme={theme} apiHost="" apiPort="" showToast={showToast} />);
  return { showToast };
}

const calls = (path, method) => apiFetch.mock.calls.filter(([, , p, o]) => p === path && (o?.method ?? 'GET') === method);
const body = (call) => JSON.parse(call[3].body);
// A select can only take a value it lists: wait for the option (the provider's models load on their own).
const pick = async (label, value) => {
  const field = await screen.findByLabelText(label);
  if (field.tagName === 'SELECT') {
    await waitFor(() => expect(screen.getByLabelText(label).querySelector(`option[value="${value}"]`)).toBeTruthy());
  }
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
};

describe('EmbeddingPicker', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the model in use and where the choice comes from', async () => {
    mount();
    expect((await screen.findByText(/In use/)).textContent).toBe('In use: nomic-embed-text on ollama.');
    expect(screen.getByText(/EMBEDDING_MODEL/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Back to the server’s setting' })).toBeNull();
  });

  it('shows a model once when the provider lists it with :latest', async () => {
    mount();
    const field = await screen.findByLabelText('Embedding model');
    await waitFor(() => expect(field.querySelector('option[value="nomic-embed-text:latest"]')).toBeTruthy());
    expect([...screen.getByLabelText('Embedding model').options].map((o) => o.value))
      .toEqual(['nomic-embed-text:latest', 'embeddinggemma-2:740m', 'gemma4:e4b']);
    expect(screen.getByLabelText('Embedding model').value).toBe('nomic-embed-text:latest');
  });

  it('checks a picked model and shows the verdict in the server’s words', async () => {
    mount({ probe: SHORTENED });
    await pick('Embedding model', 'embeddinggemma-2:740m');
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    expect(await screen.findByText(SHORTENED.reason)).toBeTruthy();
    expect(body(calls('/v1/admin/embeddings/probe', 'POST')[0])).toEqual({ provider: 'ollama', model: 'embeddinggemma-2:740m' });
  });

  it('enables Use this model only after an admitted check of what is picked, and sends the PUT', async () => {
    const { showToast } = mount({ views: [view(), view({ target: { provider: 'ollama', model: 'embeddinggemma-2:740m' },
      source: { provider: 'admin', model: 'admin' },
      migration: building(0, 40) })] });
    await pick('Embedding model', 'embeddinggemma-2:740m');
    const use = screen.getByRole('button', { name: 'Use this model' });
    expect(use.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await screen.findByText(ADMITTED.reason);
    expect(use.disabled).toBe(false);
    fireEvent.click(use);
    await waitFor(() => expect(calls('/v1/admin/embeddings', 'PUT')).toHaveLength(1));
    expect(body(calls('/v1/admin/embeddings', 'PUT')[0])).toEqual({ provider: 'ollama', model: 'embeddinggemma-2:740m' });
    expect(await screen.findByText('Re-embedding for embeddinggemma-2:740m: 0 of 40 passages (0%).')).toBeTruthy();
    expect(showToast).toHaveBeenCalled();
  });

  it('a refused model keeps Use this model off', async () => {
    mount({ probe: REFUSED });
    await pick('Embedding model', 'embeddinggemma-2:740m');
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    expect(await screen.findByText(REFUSED.reason)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use this model' }).disabled).toBe(true);
  });

  it('changing the pick after a check needs a new check', async () => {
    mount();
    await pick('Embedding model', 'embeddinggemma-2:740m');
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await screen.findByText(ADMITTED.reason);
    await pick('Embedding model', 'gemma4:e4b');
    expect(screen.getByRole('button', { name: 'Use this model' }).disabled).toBe(true);
    expect(screen.queryByText(ADMITTED.reason)).toBeNull();
  });

  it('shows a refusal from the server in its words', async () => {
    mount({ put: () => json(422, { detail: { error: 'refused_model', message: 'It changed its mind: 1024 numbers.' } }) });
    await pick('Embedding model', 'embeddinggemma-2:740m');
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await screen.findByText(ADMITTED.reason);
    fireEvent.click(screen.getByRole('button', { name: 'Use this model' }));
    expect(await screen.findByText('It changed its mind: 1024 numbers.')).toBeTruthy();
  });

  it('takes a typed name for a provider that lists no models', async () => {
    mount();
    await pick('Embedding provider', 'cloud');
    await waitFor(() => expect(screen.getByLabelText('Embedding model').tagName).toBe('INPUT'));
    fireEvent.change(screen.getByLabelText('Embedding model'), { target: { value: 'text-embedding-3-small' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await waitFor(() => expect(calls('/v1/admin/embeddings/probe', 'POST')).toHaveLength(1));
    expect(body(calls('/v1/admin/embeddings/probe', 'POST')[0])).toEqual({ provider: 'cloud', model: 'text-embedding-3-small' });
  });

  it('takes a typed name when the provider lists other models', async () => {
    mount();
    await screen.findByRole('option', { name: 'gemma4:e4b' });
    fireEvent.click(screen.getByRole('button', { name: 'Type a name' }));
    const field = screen.getByLabelText('Embedding model');
    expect(field.tagName).toBe('INPUT');
    fireEvent.change(field, { target: { value: 'openai/text-embedding-3-small' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await waitFor(() => expect(calls('/v1/admin/embeddings/probe', 'POST')).toHaveLength(1));
    expect(body(calls('/v1/admin/embeddings/probe', 'POST')[0]).model).toBe('openai/text-embedding-3-small');
    fireEvent.click(screen.getByRole('button', { name: 'Choose from the list' }));
    expect(screen.getByLabelText('Embedding model').tagName).toBe('SELECT');
  });

  it('cancels a re-embed', async () => {
    const { showToast } = mount({ views: [view({ migration: building(3, 40) })] });
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the re-embed' }));
    await waitFor(() => expect(calls('/v1/admin/embeddings/migration', 'DELETE')).toHaveLength(1));
    await waitFor(() => expect(showToast).toHaveBeenCalledTimes(1));
    expect(showToast.mock.calls[0][0]).toMatch(/^Re-embed cancelled/);      // not "done"
  });

  it('goes back to the server’s setting', async () => {
    mount({ views: [view({ target: { provider: 'ollama', model: 'embeddinggemma-2:740m' }, source: { provider: 'admin', model: 'admin' } })] });
    fireEvent.click(await screen.findByRole('button', { name: 'Back to the server’s setting' }));
    await waitFor(() => expect(calls('/v1/admin/embeddings', 'PUT')).toHaveLength(1));
    expect(body(calls('/v1/admin/embeddings', 'PUT')[0])).toEqual({ provider: null, model: null });
  });

  it('shows a paused re-embed and its reason', async () => {
    mount({ views: [view({ migration: building(3, 40, { state: 'failed', error: 'Can’t reach ollama' }) })] });
    expect(await screen.findByText('Re-embedding for embeddinggemma-2:740m is paused: Can’t reach ollama')).toBeTruthy();
  });

  it('shows a bar, the share done and the time left', async () => {
    mount({ views: [view({ migration: building(1234, 5000, { seconds_left: 400 }) })] });
    expect(await screen.findByText('Re-embedding for embeddinggemma-2:740m: 1,234 of 5,000 passages (24%), about 7 minutes left.'))
      .toBeTruthy();
    const bar = screen.getByRole('progressbar', { name: 'Re-embed progress' });
    expect([bar.getAttribute('aria-valuenow'), bar.getAttribute('aria-valuemax')]).toEqual(['1234', '5000']);
  });

  it('words the time left in seconds, minutes and hours', async () => {
    for (const [seconds, words] of [[30, 'less than a minute left'], [61, 'about 2 minutes left'],
      [60 * 60 * 1.5, 'about 1.5 hours left']]) {
      mount({ views: [view({ migration: building(1, 10, { seconds_left: seconds }) })] });
      expect(await screen.findByText(`Re-embedding for embeddinggemma-2:740m: 1 of 10 passages (10%), ${words}.`)).toBeTruthy();
      cleanup();
    }
  });

  it('says what is wrong with the model in use', async () => {
    mount({ views: [view({ current: { provider: 'ollama', model: 'mxbai-embed-large', dimensions: null,
      error: 'mxbai-embed-large gives 1024 numbers and the library holds 768' } })] });
    expect((await screen.findByRole('alert')).textContent)
      .toBe('mxbai-embed-large gives 1024 numbers and the library holds 768: documents can’t be indexed or searched until another model is chosen.');
  });
});

describe('EmbeddingPicker while re-embedding', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());
  const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  it('re-reads the progress every 5 s while one runs, then stops', async () => {
    const at = (filled) => view({ migration: building(filled) });
    mount({ views: [at(1234), at(2500), view({ current: GEMMA_IN_USE })] });
    await advance(0);
    expect(screen.getByText('Re-embedding for embeddinggemma-2:740m: 1,234 of 5,000 passages (24%).')).toBeTruthy();
    await advance(5000);
    expect(screen.getByText('Re-embedding for embeddinggemma-2:740m: 2,500 of 5,000 passages (50%).')).toBeTruthy();
    await advance(5000);
    expect(screen.queryByText(/Re-embedding for/)).toBeNull();
    const reads = calls('/v1/admin/embeddings', 'GET').length;
    await advance(15000);
    expect(calls('/v1/admin/embeddings', 'GET')).toHaveLength(reads);   // done: no more polling
  });

  it('says so when the re-embed finishes', async () => {
    const { showToast } = mount({ views: [view({ migration: building(4990) }),
      view({ migration: building(5000, 5000, { state: 'switching' }) }), view({ current: GEMMA_IN_USE })] });
    await advance(0);
    expect(showToast).not.toHaveBeenCalled();
    await advance(5000);
    expect(screen.getByText('Re-embedding for embeddinggemma-2:740m: every passage done, switching now.')).toBeTruthy();
    await advance(5000);
    expect(showToast).toHaveBeenCalledWith('Re-embed done: search now uses embeddinggemma-2:740m.', 8000);
    expect(screen.getByText(/In use/).textContent).toBe('In use: embeddinggemma-2:740m on ollama.');
  });

  it('says so when the re-embed pauses, and when it was stopped elsewhere', async () => {
    const { showToast } = mount({ views: [view({ migration: building(10) }),
      view({ migration: building(10, 5000, { state: 'failed', error: 'Can’t reach ollama' }) }), view()] });
    await advance(0);
    await advance(5000);
    expect(showToast).toHaveBeenLastCalledWith('Re-embed paused: Can’t reach ollama', 8000);
    await advance(5000);   // cancelled from another tab: the library stayed on nomic
    expect(showToast).toHaveBeenLastCalledWith('Re-embed stopped: the library stays on nomic-embed-text.', 8000);
  });
});
