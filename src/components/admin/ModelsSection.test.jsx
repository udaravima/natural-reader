import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ModelsSection } from './ModelsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const row = (env, value, fallback = value, over = {}) => ({
  value, source: 'default', env, fallback, fallback_source: 'default', ...over,
});
const settings = (over = {}) => ({
  reader: row('READER_MODEL', 'chat'),
  context_size: row('INFERENCE_LOCAL_NUM_CTX', null),
  context_sizes: row(null, {}),
  context_windows: row(null, {}),
  provider_locality: row(null, {}),
  chat_models: row(null, []),
  ...over,
});
const INFO = [
  { id: 'num_ctx_max', label: 'Largest context size', value: '32,768 tokens', env: 'INFERENCE_NUM_CTX_MAX', source: 'default' },
  { id: 'keep_alive_max', label: 'Longest time a model stays loaded', value: 'No limit', env: 'INFERENCE_KEEP_ALIVE_MAX', source: 'default' },
];
const view = (over = {}) => ({ settings: settings(over), info: INFO });

const model = (provider, name, over = {}) => ({
  id: `${provider}:${name}`, provider, kind: 'ollama', name, reported_window: 131072, cloud_name: false, ...over,
});
const CATALOG = {
  providers: [
    { name: 'ollama', kind: 'ollama', host: 'ollama:11434', detected: 'local', reachable: true },
    { name: 'openrouter', kind: 'openai', host: 'openrouter.example.com', detected: 'cloud', reachable: true },
    { name: 'lab', kind: 'openai', host: 'lab.example.com', detected: 'cloud', reachable: false },
  ],
  models: [
    model('ollama', 'gemma4:e4b'),
    model('ollama', 'qwen3:8b', { reported_window: null }),
    model('ollama', 'gpt-oss:120b-cloud', { reported_window: null, cloud_name: true }),
    model('openrouter', 'some/model', { kind: 'openai', reported_window: null }),
  ],
  limits: { num_ctx_max: 32768 },
};

function mount({ initial = view(), catalog = CATALOG, put } = {}) {
  const onDirtyChange = vi.fn();
  const showToast = vi.fn();
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path === '/v1/admin/models') return catalog ? json(200, catalog) : json(500, {});
    if (path !== '/v1/admin/settings/models') return json(404, {});
    if (opts?.method === 'PUT') return put ? put(opts) : json(200, initial);
    return json(200, initial);
  });
  render(<ModelsSection theme={theme} apiHost="" apiPort="" showToast={showToast} onDirtyChange={onDirtyChange} />);
  return { onDirtyChange, showToast };
}

const puts = () => apiFetch.mock.calls.filter(([, , , o]) => o?.method === 'PUT');
const body = () => JSON.parse(puts().at(-1)[3].body);
const save = () => screen.getByRole('button', { name: 'Save' });
const text = (id) => document.getElementById(id).textContent;
const sizes = () => screen.getByRole('group', { name: 'Per-model sizes' });
const windows = () => screen.getByRole('group', { name: 'Declared context windows' });
const ready = () => screen.findByLabelText('Reader');

describe('ModelsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the six rows from a settings payload and a catalog', async () => {
    mount();
    await ready();
    expect(screen.getByRole('region', { name: 'Models' })).toBeTruthy();
    const reader = screen.getByLabelText('Reader');
    expect([...reader.options].map((o) => o.textContent)).toEqual([
      'The chat model of each conversation',
      'ollama:gemma4:e4b', 'ollama:qwen3:8b', 'ollama:gpt-oss:120b-cloud', 'openrouter:some/model',
    ]);
    expect(reader.value).toBe('chat');
    expect(text('models-reader-help')).toBe('The model that reads documents and web pages for the chat model.');
    expect(screen.getByLabelText('Context size for local Ollama models')).toHaveValue(null);
    expect(text('models-context-size-help')).toBe(
      "One size for every request to a local Ollama model, so it isn't reloaded. Empty: each person's own setting, else 16,384.",
    );
    expect(text('models-context-size-limit')).toBe('At most 32,768 (INFERENCE_NUM_CTX_MAX).');
    expect(within(sizes()).getAllByRole('spinbutton')).toHaveLength(2);   // gemma4:e4b and qwen3:8b only
    expect(within(windows()).getAllByRole('spinbutton')).toHaveLength(3);  // the three that report no window
    expect(text('models-context-windows-help')).toBe("What history is trimmed to when the provider doesn't say.");
    expect(screen.getByLabelText('ollama')).toHaveValue('auto');
    expect(screen.getByRole('group', { name: 'Chat models offered' })).toBeTruthy();
    expect(within(screen.getByRole('group', { name: 'Chat models offered' })).getAllByRole('checkbox')).toHaveLength(4);
  });

  it('says 16,384 clamped to the ceiling when the ceiling is lower', async () => {
    mount({ catalog: { ...CATALOG, limits: { num_ctx_max: 8192 } } });
    await ready();
    expect(text('models-context-size-help')).toContain('else 8,192.');
  });

  it('saves a reader choice', async () => {
    mount({ put: () => json(200, view({ reader: row('READER_MODEL', 'ollama:gemma4:e4b', 'chat', { source: 'admin' }) })) });
    fireEvent.change(await ready(), { target: { value: 'ollama:gemma4:e4b' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { reader: 'ollama:gemma4:e4b' } });
  });

  it('toasts that a save applies from the next message', async () => {
    const { showToast } = mount();
    fireEvent.change(await ready(), { target: { value: 'ollama:gemma4:e4b' } });
    fireEvent.click(save());
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('Model settings saved. They apply from the next message.', 4000));
  });

  it('saves the deployment context size, and an empty one removes it', async () => {
    mount({ initial: view({ context_size: row('INFERENCE_LOCAL_NUM_CTX', 24576, null, { source: 'admin' }) }) });
    const input = await screen.findByLabelText('Context size for local Ollama models');
    expect(input).toHaveValue(24576);
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { context_size: null } });
  });

  it('typing a per-model size PUTs context_sizes with only that key added', async () => {
    mount({ initial: view({ context_sizes: row(null, { 'ollama:other': 4096 }, {}, { source: 'admin' }) }) });
    await ready();
    fireEvent.change(within(sizes()).getByLabelText('ollama:gemma4:e4b'), { target: { value: '8192' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { context_sizes: { 'ollama:other': 4096, 'ollama:gemma4:e4b': 8192 } } });
  });

  it('clearing a per-model size removes the key', async () => {
    mount({ initial: view({
      context_sizes: row(null, { 'ollama:gemma4:e4b': 8192, 'ollama:qwen3:8b': 4096 }, {}, { source: 'admin' }),
    }) });
    await ready();
    const input = within(sizes()).getByLabelText('ollama:gemma4:e4b');
    expect(input).toHaveValue(8192);
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { context_sizes: { 'ollama:qwen3:8b': 4096 } } });
  });

  it('keeps Save off for an invalid size, says why, and clears once it is put right', async () => {
    mount();
    await ready();
    const input = within(sizes()).getByLabelText('ollama:gemma4:e4b');
    fireEvent.change(input, { target: { value: '100' } });
    expect(save()).toBeDisabled();
    const message = 'Must be a whole number of tokens from 2,048 to 2,097,152.';
    expect(screen.getByText(message)).toBeTruthy();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription(expect.stringContaining(message));
    fireEvent.change(input, { target: { value: '4096' } });
    expect(screen.queryByText(message)).toBeNull();
    expect(save()).toBeEnabled();
  });

  it('keeps Save off for an invalid deployment size', async () => {
    mount();
    fireEvent.change(await screen.findByLabelText('Context size for local Ollama models'), { target: { value: '5' } });
    expect(save()).toBeDisabled();
    expect(screen.getByText('Must be a whole number of tokens from 2,048 to 2,097,152.')).toBeTruthy();
  });

  it('saves a declared window, and rejects one below 1,024', async () => {
    mount();
    await ready();
    const input = within(windows()).getByLabelText('openrouter:some/model');
    fireEvent.change(input, { target: { value: '10' } });
    expect(save()).toBeDisabled();
    expect(screen.getByText('Must be a whole number of tokens from 1,024 to 10,000,000.')).toBeTruthy();
    fireEvent.change(input, { target: { value: '8192' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { context_windows: { 'openrouter:some/model': 8192 } } });
  });

  it('setting a provider to Cloud removes its models from the per-model sizes list before saving', async () => {
    mount();
    await ready();
    expect(within(sizes()).getByLabelText('ollama:gemma4:e4b')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('ollama'), { target: { value: 'cloud' } });
    expect(within(sizes()).queryByLabelText('ollama:gemma4:e4b')).toBeNull();
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { provider_locality: { ollama: 'cloud' } } });
  });

  it('never sizes an Ollama cloud model, or any model of a provider on ollama.com', async () => {
    mount({ catalog: {
      ...CATALOG,
      providers: [
        { name: 'hosted', kind: 'ollama', host: 'api.ollama.com', detected: 'cloud', reachable: true },
        { name: 'mine', kind: 'ollama', host: 'gpu.example.org', detected: 'cloud', reachable: true },
      ],
      models: [model('hosted', 'llama3'), model('mine', 'llama3'), model('mine', 'x-cloud', { cloud_name: true })],
    } });
    await ready();
    expect(within(sizes()).getAllByRole('spinbutton')).toHaveLength(1);
    expect(within(sizes()).getByLabelText('mine:llama3')).toBeTruthy();   // a self-hosted Ollama on any host is sized
  });

  it('drops a provider back to Auto by removing its key', async () => {
    mount({ initial: view({ provider_locality: row(null, { ollama: 'cloud', lab: 'local' }, {}, { source: 'admin' }) }) });
    await ready();
    const select = screen.getByLabelText('ollama');
    expect(select).toHaveValue('cloud');
    fireEvent.change(select, { target: { value: 'auto' } });
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { provider_locality: { lab: 'local' } } });
  });

  it('labels Auto with what the host rule detected, and shows kind, host and reachability', async () => {
    mount();
    await ready();
    expect([...screen.getByLabelText('ollama').options].map((o) => o.textContent)).toEqual(['Auto (local)', 'Local', 'Cloud']);
    expect([...screen.getByLabelText('openrouter').options].map((o) => o.textContent)).toEqual(['Auto (cloud)', 'Local', 'Cloud']);
    expect(screen.getByLabelText('ollama')).toHaveAccessibleDescription(expect.stringContaining('ollama · ollama:11434'));
    expect(screen.getAllByText("Can't be reached.")).toHaveLength(1);
    expect(screen.getByLabelText('lab')).toHaveAccessibleDescription(expect.stringContaining("Can't be reached."));
    expect(text('models-provider-locality-help')).toBe(
      "Local or cloud decides what counts as cloud usage. A cloud provider's Ollama models are not sized.",
    );
  });

  it('says every allowed model is offered when none is ticked, and saves the ticked ones', async () => {
    mount();
    await ready();
    const group = screen.getByRole('group', { name: 'Chat models offered' });
    expect(text('models-chat-models-none')).toBe('Every model the server allows.');
    expect(text('models-chat-models-help')).toBe(
      'People can chat only with the ticked models. None ticked: every model the server allows.',
    );
    fireEvent.click(within(group).getByRole('checkbox', { name: 'openrouter:some/model' }));
    fireEvent.click(within(group).getByRole('checkbox', { name: 'ollama:gemma4:e4b' }));
    expect(document.getElementById('models-chat-models-none')).toBeNull();
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { chat_models: ['ollama:gemma4:e4b', 'openrouter:some/model'] } });
  });

  it('keeps a stored chat-model id the catalog no longer lists when another is toggled', async () => {
    mount({ initial: view({ chat_models: row(null, ['gone:model'], [], { source: 'admin' }) }) });
    await ready();
    fireEvent.click(screen.getByRole('checkbox', { name: 'ollama:gemma4:e4b' }));
    fireEvent.click(save());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(body()).toEqual({ changes: { chat_models: ['gone:model', 'ollama:gemma4:e4b'] } });
  });

  it('says the models could not load while the settings rows still render', async () => {
    mount({ catalog: null });
    expect(await screen.findByText("Couldn't load the models.")).toBeTruthy();
    expect(screen.getByLabelText('Reader')).toBeTruthy();
    expect(screen.getByLabelText('Context size for local Ollama models')).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Chat models offered' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Providers' })).toBeTruthy();
  });

  it('keeps the current reader choosable when the catalog does not list it', async () => {
    mount({ catalog: null, initial: view({ reader: row('READER_MODEL', 'ollama:old', 'chat', { source: 'env' }) }) });
    const reader = await ready();
    expect(reader).toHaveValue('ollama:old');
  });

  it('announces a refusal with role=alert and keeps the drafts', async () => {
    mount({ put: () => json(400, { detail: 'Choose the chat model, or a model from the list.' }) });
    fireEvent.change(await ready(), { target: { value: 'ollama:gemma4:e4b' } });
    fireEvent.click(save());
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose the chat model, or a model from the list.');
    expect(screen.getByLabelText('Reader')).toHaveValue('ollama:gemma4:e4b');
  });

  it('ties help and source lines to each control, and names every control', async () => {
    mount();
    await ready();
    expect(screen.getByLabelText('Reader')).toHaveAttribute('aria-describedby', 'models-reader-help models-reader-source');
    expect(screen.getByLabelText('Context size for local Ollama models')).toHaveAccessibleDescription(
      expect.stringContaining('At most 32,768'),
    );
    for (const el of document.querySelectorAll('input, select')) {
      expect(el).toHaveAccessibleName();
    }
  });

  it('reports unsaved changes', async () => {
    const { onDirtyChange } = mount();
    fireEvent.change(await ready(), { target: { value: 'ollama:gemma4:e4b' } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
  });

  it('says the settings could not load', async () => {
    apiFetch.mockImplementation(async () => json(500, {}));
    render(<ModelsSection theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    expect(await screen.findByText("Couldn't load the model settings.")).toBeTruthy();
  });
});
