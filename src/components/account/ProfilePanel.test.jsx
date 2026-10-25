import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
vi.mock('../../lib/squareImage', () => ({ squareImage: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';
import { squareImage } from '../../lib/squareImage';
import { ProfilePanel } from './ProfilePanel';

const theme = { border: '', bgSecondary: '', text: '', textSecondary: '', textMuted: '' };
const hour = 3600 * 1000;
const BROWSERS = [
  { id: 'h1', current: true, user_agent: 'Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0 Safari/537.36',
    created_at: new Date(Date.now() - 48 * hour).toISOString(), last_seen_at: new Date().toISOString() },
  { id: 'h2', current: false, user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Safari/604.1',
    created_at: new Date(Date.now() - 72 * hour).toISOString(), last_seen_at: new Date(Date.now() - 3 * hour).toISOString() },
  { id: 'h3', current: false, user_agent: 'Mozilla/5.0 (Windows NT 10.0) Firefox/131.0',
    created_at: new Date(Date.now() - 96 * hour).toISOString(), last_seen_at: new Date(Date.now() - 30 * hour).toISOString() },
];
const ok = (body) => ({ ok: true, status: 200, json: async () => body });

function routes(extra = {}) {
  apiFetch.mockImplementation(async (_h, _p, path, init = {}) => {
    const key = `${init.method || 'GET'} ${path}`;
    if (extra[key]) return extra[key](init);
    if (key === 'GET /v1/auth/sessions') return ok(BROWSERS);
    return { ok: true, status: 204, json: async () => ({}) };
  });
}

const user = (over = {}) => ({ id: 'u', name: 'Asha Perera', email: 'asha@example.com', avatar_url: null,
  account_url: 'https://auth.example.com/realms/r/account', ...over });

describe('ProfilePanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('names you, links to the sign-in account page, and lists signed-in browsers', async () => {
    routes();
    render(<ProfilePanel theme={theme} apiHost="" apiPort="" user={user()} />);
    expect(screen.getByText('Asha Perera')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /manage your account/i }))
      .toHaveAttribute('href', 'https://auth.example.com/realms/r/account');
    expect(await screen.findByText('Chrome on Linux')).toBeInTheDocument();
    expect(screen.getByText('this browser')).toBeInTheDocument();
    expect(screen.getByText('Safari on iOS')).toBeInTheDocument();
    expect(screen.getByText(/Last used 3 hours ago/)).toBeInTheDocument();
  });

  it('without an account page, says who to ask', () => {
    routes();
    render(<ProfilePanel theme={theme} apiHost="" apiPort="" user={user({ account_url: null })} />);
    expect(screen.queryByRole('link', { name: /manage your account/i })).toBeNull();
    expect(screen.getByText(/ask your administrator/i)).toBeInTheDocument();
  });

  it('signs out one browser, then every other one, never this one', async () => {
    routes({ 'POST /v1/auth/sessions/sign-out-others': () => ok({ signed_out: 1 }) });
    render(<ProfilePanel theme={theme} apiHost="" apiPort="" user={user()} />);
    await screen.findByText('Safari on iOS');
    expect(screen.getAllByRole('button', { name: 'Sign out' })).toHaveLength(2);
    fireEvent.click(screen.getAllByRole('button', { name: 'Sign out' })[0]);
    await waitFor(() => expect(screen.queryByText('Safari on iOS')).toBeNull());
    expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/auth/sessions/h2', { method: 'DELETE' });
    fireEvent.click(screen.getByRole('button', { name: /sign out everywhere else/i }));
    await waitFor(() => expect(screen.queryByText('Firefox on Windows')).toBeNull());
    expect(screen.getByText('Chrome on Linux')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /sign out everywhere else/i })).toBeNull();
  });

  it('uploads a chosen picture, resized, and shows it', async () => {
    const blob = new Blob(['x'], { type: 'image/webp' });
    squareImage.mockResolvedValue(blob);
    routes({ 'PUT /v1/auth/me/avatar': () => ok({ avatar_url: '/v1/users/u/avatar?v=9' }) });
    const onUserChanged = vi.fn();
    render(<ProfilePanel theme={theme} apiHost="" apiPort="" user={user()} onUserChanged={onUserChanged} />);
    const file = new File(['big photo'], 'me.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByLabelText('Picture file'), { target: { files: [file] } });
    await waitFor(() => expect(onUserChanged).toHaveBeenCalledWith({ avatar_url: '/v1/users/u/avatar?v=9' }));
    expect(squareImage).toHaveBeenCalledWith(file);
    const put = apiFetch.mock.calls.find(([, , p, i]) => p === '/v1/auth/me/avatar' && i?.method === 'PUT');
    expect(put[3]).toMatchObject({ headers: { 'Content-Type': 'image/webp' }, body: blob });
  });

  it("shows the server's refusal and keeps the old picture", async () => {
    squareImage.mockResolvedValue(new Blob(['x'], { type: 'image/webp' }));
    routes({ 'PUT /v1/auth/me/avatar': () => ({ ok: false, status: 413,
      json: async () => ({ detail: { message: 'That picture is over 256 KB. Pick a smaller one.' } }) }) });
    const onUserChanged = vi.fn();
    render(<ProfilePanel theme={theme} apiHost="" apiPort="" user={user()} onUserChanged={onUserChanged} />);
    fireEvent.change(screen.getByLabelText('Picture file'), { target: { files: [new File(['x'], 'a.png')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('over 256 KB');
    expect(onUserChanged).not.toHaveBeenCalled();
  });

  it('removes the picture', async () => {
    routes();
    const onUserChanged = vi.fn();
    render(<ProfilePanel theme={theme} apiHost="" apiPort="" user={user({ avatar_url: '/v1/users/u/avatar?v=1' })}
      onUserChanged={onUserChanged} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(onUserChanged).toHaveBeenCalledWith({ avatar_url: null }));
  });
});
