import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { AdminConsole } from './AdminConsole';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const noContent = () => ({ ok: true, status: 204 });

const user = (over = {}) => ({
  id: 'u1', email: 'a@x.io', display_name: null, role: 'member', status: 'active',
  oidc_iss: 'https://kc', oidc_sub: 'sub-1',
  inference_daily_token_budget: null, created_at: '2026-09-17T00:00:00Z',
  ...over,
});

// Mounts the console with a mutable user list so PATCH/POST/DELETE handlers
// can mutate it and the reload reflects the change, like the real backend.
function mount({ users, usage = [], config = {}, showToast = vi.fn(), currentUserId = 'me' } = {}) {
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path === '/v1/admin/users' && opts?.method === 'POST') {
      const body = JSON.parse(opts.body);
      if (users.some((u) => u.email === body.email)) return json(409, { detail: 'email already exists' });
      const created = user({ id: `u${users.length + 1}`, ...body, oidc_sub: null });
      users.push(created);
      return json(201, created);
    }
    if (path.startsWith('/v1/admin/users/') && opts?.method === 'PATCH') return json(200, {});
    if (path.startsWith('/v1/admin/users/') && opts?.method === 'DELETE') {
      const id = path.split('/').pop();
      const i = users.findIndex((u) => u.id === id);
      if (i >= 0) users.splice(i, 1);
      return noContent();
    }
    if (path === '/v1/admin/users') return json(200, users);
    if (path.startsWith('/v1/admin/inference/usage')) return json(200, usage);
    if (path === '/v1/admin/inference/config') return json(200, config);
    return json(404, {});
  });
  render(
    <AdminConsole
      theme={theme} apiHost="" apiPort="" currentUserId={currentUserId}
      onBack={vi.fn()} showToast={showToast}
    />
  );
}

const calls = (method) =>
  apiFetch.mock.calls.filter(([, , , opts]) => opts?.method === method);
const lastBody = (method) => JSON.parse(calls(method).at(-1)[3].body);

describe('AdminConsole — users', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders rows and the awaiting-first-login badge for unlinked rows', async () => {
    mount({ users: [user(), user({ id: 'u2', email: 'b@x.io', oidc_sub: null, status: 'pending' })] });
    expect(await screen.findByText('a@x.io')).toBeInTheDocument();
    expect(screen.getByText('awaiting first login')).toBeInTheDocument();
    expect(screen.getByText(/member\/pending/)).toBeInTheDocument();
    expect(within(screen.getByTestId('user-row-u2')).getByText(/2026-09-17/)).toBeInTheDocument();
  });

  it('PATCHes status without the budget field when untouched (exclude_unset)', async () => {
    mount({ users: [user({ status: 'pending' })] });
    fireEvent.click(await screen.findByRole('button', { name: /activate/i }));
    await waitFor(() => expect(calls('PATCH').length).toBeGreaterThan(0));
    expect(lastBody('PATCH')).toEqual({ status: 'active' });
  });

  it('sends 0 as unlimited and empty as an explicit null clear', async () => {
    mount({ users: [user()] });
    await screen.findByText('a@x.io');
    const input = screen.getByLabelText('Budget for a@x.io');
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(1));
    expect(lastBody('PATCH')).toEqual({ inference_daily_token_budget: 0 });
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(2));
    expect(lastBody('PATCH')).toEqual({ inference_daily_token_budget: null });
  });

  it('hides Disable, role toggle and Delete on the self row', async () => {
    mount({ users: [user({ id: 'me', role: 'admin' })] });
    await screen.findByText('a@x.io');
    const row = screen.getByTestId('user-row-me');
    expect(within(row).queryByRole('button', { name: /disable/i })).toBeNull();
    expect(within(row).queryByRole('button', { name: /make member/i })).toBeNull();
    expect(within(row).queryByRole('button', { name: /delete/i })).toBeNull();
  });

  it('delete confirm requires the typed email, then DELETEs and toasts', async () => {
    const showToast = vi.fn();
    const users = [user({ id: 'me', role: 'admin' }), user({ id: 'u2', email: 'b@x.io', oidc_sub: null })];
    mount({ users, showToast });
    await screen.findByText('b@x.io');
    const row = screen.getByTestId('user-row-u2');
    fireEvent.click(within(row).getByRole('button', { name: /delete/i }));
    const confirm = within(row).getByRole('button', { name: /confirm delete/i });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(row).getByLabelText(/confirm email/i), { target: { value: 'wrong@x.io' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(row).getByLabelText(/confirm email/i), { target: { value: 'b@x.io' } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(calls('DELETE').length).toBe(1));
    expect(calls('DELETE')[0][2]).toBe('/v1/admin/users/u2');
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('documents and chat history'), expect.anything()
    ));
  });

  it('enrolls a pre-provisioned user and shows the claim instruction', async () => {
    const users = [user({ id: 'me', role: 'admin' })];
    mount({ users });
    await screen.findByText('a@x.io');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'new@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(lastBody('POST')).toEqual({ email: 'new@x.io' });
    expect(await screen.findByText(/have the user log in with this exact verified email/i)).toBeInTheDocument();
    expect(await screen.findByText('awaiting first login')).toBeInTheDocument();
  });

  it('renders the duplicate-email 409 inline', async () => {
    const users = [user({ id: 'me', role: 'admin', email: 'me@x.io' })];
    mount({ users });
    await screen.findByText('me@x.io');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'me@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    expect(await screen.findByText(/already taken/i)).toBeInTheDocument();
  });
});

describe('AdminConsole — usage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders ISO day rows as-is', async () => {
    mount({
      users: [user()],
      usage: [{ email: 'a@x.io', user_id: 'u1', day: '2026-09-17', prompt_tokens: 10, eval_tokens: 5, requests: 2, tokens: 15 }],
    });
    expect(await screen.findByText('2026-09-17')).toBeInTheDocument();
    expect(screen.getByText('15')).toBeInTheDocument();
  });

  it('shows the empty state and re-fetches when the days selector changes', async () => {
    mount({ users: [user()], usage: [] });
    expect(await screen.findByText('No usage recorded yet.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/usage days/i), { target: { value: '30' } });
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/inference/usage?days=30')
    );
  });
});

describe('AdminConsole — config', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders read-only values, with "all models allowed" for an empty allowlist', async () => {
    mount({
      users: [user()],
      config: {
        ollama_url: 'http://ollama:11434', timeout_s: 120, allowed_models: null,
        summarize_model: 'sum', embed_model: 'emb', daily_token_budget: null,
      },
    });
    expect(await screen.findByText('http://ollama:11434')).toBeInTheDocument();
    expect(screen.getByText('all models allowed')).toBeInTheDocument();
    expect(screen.getByText('unlimited (unset)')).toBeInTheDocument();
  });

  it('renders an explicit allowlist', async () => {
    mount({
      users: [user()],
      config: { ollama_url: 'http://x:11434', timeout_s: 60, allowed_models: ['llama3.2:3b', 'qwen3.5'], summarize_model: null, embed_model: null, daily_token_budget: 1000 },
    });
    expect(await screen.findByText('llama3.2:3b, qwen3.5')).toBeInTheDocument();
    expect(screen.getByText('1000 tokens/day')).toBeInTheDocument();
  });
});
