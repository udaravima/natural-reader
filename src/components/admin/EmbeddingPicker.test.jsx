import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { EmbeddingPicker } from './EmbeddingPicker';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const NONE = { state: 'none', model: null, filled: 0, total: 0, error: null };
const view = (over = {}) => ({
  current: { provider: 'ollama', model: 'nomic-embed-text', dimensions: null },
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
      migration: { state: 'building', model: 'embeddinggemma-2:740m', filled: 0, total: 40, error: null } })] });
    await pick('Embedding model', 'embeddinggemma-2:740m');
    const use = screen.getByRole('button', { name: 'Use this model' });
    expect(use.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await screen.findByText(ADMITTED.reason);
    expect(use.disabled).toBe(false);
    fireEvent.click(use);
    await waitFor(() => expect(calls('/v1/admin/embeddings', 'PUT')).toHaveLength(1));
    expect(body(calls('/v1/admin/embeddings', 'PUT')[0])).toEqual({ provider: 'ollama', model: 'embeddinggemma-2:740m' });
    expect(await screen.findByText('Re-embedding for embeddinggemma-2:740m: 0 of 40 passages.')).toBeTruthy();
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

  it('cancels a re-embed', async () => {
    mount({ views: [view({ migration: { state: 'building', model: 'embeddinggemma-2:740m', filled: 3, total: 40, error: null } })] });
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the re-embed' }));
    await waitFor(() => expect(calls('/v1/admin/embeddings/migration', 'DELETE')).toHaveLength(1));
  });

  it('goes back to the server’s setting', async () => {
    mount({ views: [view({ target: { provider: 'ollama', model: 'embeddinggemma-2:740m' }, source: { provider: 'admin', model: 'admin' } })] });
    fireEvent.click(await screen.findByRole('button', { name: 'Back to the server’s setting' }));
    await waitFor(() => expect(calls('/v1/admin/embeddings', 'PUT')).toHaveLength(1));
    expect(body(calls('/v1/admin/embeddings', 'PUT')[0])).toEqual({ provider: null, model: null });
  });

  it('shows a paused re-embed and its reason', async () => {
    mount({ views: [view({ migration: { state: 'failed', model: 'embeddinggemma-2:740m', filled: 3, total: 40, error: 'Can’t reach ollama' } })] });
    expect(await screen.findByText('Re-embedding for embeddinggemma-2:740m is paused: Can’t reach ollama')).toBeTruthy();
  });
});

describe('EmbeddingPicker while re-embedding', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());
  const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  it('re-reads the progress every 5 s while one runs, then stops', async () => {
    const building = (filled) => view({ migration: { state: 'building', model: 'embeddinggemma-2:740m', filled, total: 5000, error: null } });
    mount({ views: [building(1234), building(2500), view({ current: { provider: 'ollama', model: 'embeddinggemma-2:740m', dimensions: null } })] });
    await advance(0);
    expect(screen.getByText('Re-embedding for embeddinggemma-2:740m: 1,234 of 5,000 passages.')).toBeTruthy();
    await advance(5000);
    expect(screen.getByText('Re-embedding for embeddinggemma-2:740m: 2,500 of 5,000 passages.')).toBeTruthy();
    await advance(5000);
    expect(screen.queryByText(/Re-embedding for/)).toBeNull();
    const reads = calls('/v1/admin/embeddings', 'GET').length;
    await advance(15000);
    expect(calls('/v1/admin/embeddings', 'GET')).toHaveLength(reads);   // done: no more polling
  });
});
