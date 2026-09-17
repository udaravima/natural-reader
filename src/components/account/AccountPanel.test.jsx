import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AccountPanel } from './AccountPanel';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const json = (status, body) => new Response(JSON.stringify(body), { status });
const baseProps = (over = {}) => ({ theme, apiHost: '', apiPort: '', user: { email: 'a@x.io', role: 'admin' }, onLogout: vi.fn(), ...over });

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
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't2', name: 'ext', last_used_at: null, expires_at: null }])); // reload
    render(<AccountPanel {...baseProps()} />);
    fireEvent.change(await screen.findByPlaceholderText(/token name/i), { target: { value: 'ext' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    expect(await screen.findByText('nrp_secret')).toBeInTheDocument();
  });

  it('revokes a token via DELETE', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 't1', name: 'cli', last_used_at: null, expires_at: null }]));
    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 })); // delete
    apiFetch.mockResolvedValueOnce(json(200, []));                     // reload
    render(<AccountPanel {...baseProps()} />);
    fireEvent.click(await screen.findByRole('button', { name: /revoke/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/auth/tokens/t1', expect.objectContaining({ method: 'DELETE' })));
  });
});
