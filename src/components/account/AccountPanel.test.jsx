import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AccountPanel } from './AccountPanel';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const json = (status, body) => new Response(JSON.stringify(body), { status });
const baseProps = (over = {}) => ({ theme, apiHost: '', apiPort: '', user: { email: 'a@x.io', role: 'admin', capabilities: ['admin', 'chat', 'reader'] }, ...over });
const createBody = () => JSON.parse(apiFetch.mock.calls.find(([, , path, opts]) => path === '/v1/auth/tokens' && opts?.method === 'POST')[3].body);

describe('AccountPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists existing tokens on mount', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't1', name: 'cli', last_used_at: null, expires_at: null }]));
    render(<AccountPanel {...baseProps()} />);
    expect(await screen.findByText('cli')).toBeInTheDocument();
  });

  it('shows the raw token exactly once after create', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));                                  // initial list
    apiFetch.mockResolvedValueOnce(json(200, { id: 't2', token: 'nrp_secret' }));   // create
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'ext' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    expect(await screen.findByText('nrp_secret')).toBeInTheDocument();
  });

  it('revokes a token via DELETE', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't1', name: 'cli', last_used_at: null, expires_at: null }]));
    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 })); // delete
    render(<AccountPanel {...baseProps()} />);
    fireEvent.click(await screen.findByRole('button', { name: /revoke/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/auth/tokens/t1', expect.objectContaining({ method: 'DELETE' })));
  });

  it('does not render a Log out button (logout lives in the profile menu)', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));
    render(<AccountPanel {...baseProps()} />);
    await screen.findByPlaceholderText(/token name/i);
    expect(screen.queryByRole('button', { name: /log out/i })).toBeNull();
  });

  it('creates a read-aloud-only token expiring in a year by default', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));
    apiFetch.mockResolvedValueOnce(json(200, { id: 't3', token: 'nrp_x' }));
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'ext' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    await screen.findByText('nrp_x');
    const body = createBody();
    expect(body.scopes).toEqual(['read_aloud']);
    const days = (new Date(body.expires_at) - Date.now()) / 86400000;
    expect(days).toBeGreaterThan(364);
    expect(days).toBeLessThan(366.1);
  });

  it('sends the ticked scopes and no expiry for Never', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));
    apiFetch.mockResolvedValueOnce(json(200, { id: 't4', token: 'nrp_y' }));
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'bot' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /^read aloud/i }));
    fireEvent.click(screen.getByRole('checkbox', { name: /chat/i }));
    fireEvent.change(screen.getByLabelText(/expires/i), { target: { value: 'never' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    await screen.findByText('nrp_y');
    const body = createBody();
    expect(body.scopes).toEqual(['chat']);
    expect(body.expires_at).toBeNull();
  });

  it('cannot create a token with no access ticked', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /^read aloud/i }));
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
  });

  it('offers only the scopes the account holds', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));
    render(<AccountPanel {...baseProps({ user: { email: 'm@x.io', role: 'member', capabilities: ['reader'] } })} />);
    await screen.findByPlaceholderText(/token name/i);
    expect(screen.getByRole('checkbox', { name: /^read aloud/i })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /admin/i })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /chat/i })).toBeNull();
  });

  it('labels each token with its access, and old tokens as full access', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [
      { id: 'a', name: 'ext', scopes: ['read_aloud'], last_used_at: null, expires_at: null },
      { id: 'b', name: 'old-cli', scopes: null, last_used_at: null, expires_at: null },
    ]));
    render(<AccountPanel {...baseProps()} />);
    expect(await screen.findByText(/full access \(old\)/i)).toBeInTheDocument();
    expect(screen.getByText('Read aloud', { selector: '[data-scope-label]' })).toBeInTheDocument();
  });

  it('shows why the server refused to create a token', async () => {
    apiFetch.mockResolvedValueOnce(json(200, []));
    apiFetch.mockResolvedValueOnce(json(422, { detail: { error: 'scope_not_held', message: "Your account can't grant admin." } }));
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    expect(await screen.findByText(/can't grant admin/i)).toBeInTheDocument();
  });

  // The server answers a write before its transaction commits, so a list read
  // right after can miss the change: the panel updates itself from the answer.
  it('adds a created token to the list without re-reading it', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'a', name: 'first', scopes: ['read_aloud'], last_used_at: null, expires_at: null }]));
    apiFetch.mockResolvedValueOnce(json(200, { id: 'b', token: 'nrp_z' }));
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'second' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    expect(await screen.findByText('second')).toBeInTheDocument();
    expect(screen.getByText('first')).toBeInTheDocument();
    expect(apiFetch.mock.calls.filter(([, , path, opts]) => path === '/v1/auth/tokens' && !opts)).toHaveLength(1);
  });

  it('drops a revoked token from the list without re-reading it', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [
      { id: 'a', name: 'keep', scopes: ['read_aloud'], last_used_at: null, expires_at: null },
      { id: 'b', name: 'gone', scopes: ['chat'], last_used_at: null, expires_at: null },
    ]));
    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    render(<AccountPanel {...baseProps()} />);
    await screen.findByText('gone');
    fireEvent.click(screen.getAllByRole('button', { name: /revoke/i })[1]);
    await waitFor(() => expect(screen.queryByText('gone')).toBeNull());
    expect(screen.getByText('keep')).toBeInTheDocument();
  });
});
