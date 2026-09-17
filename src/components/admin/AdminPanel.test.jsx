import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AdminPanel } from './AdminPanel';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const json = (status, body) => new Response(JSON.stringify(body), { status });
const props = (over = {}) => ({ theme, apiHost: '', apiPort: '', currentUserId: 'me', ...over });

describe('AdminPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists users on mount', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'u2', email: 'b@x.io', role: 'member', status: 'pending' }]));
    render(<AdminPanel {...props()} />);
    expect(await screen.findByText('b@x.io')).toBeInTheDocument();
  });

  it('activates a pending user via PATCH', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'u2', email: 'b@x.io', role: 'member', status: 'pending' }]));
    apiFetch.mockResolvedValueOnce(json(200, { ok: true })); // patch
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'u2', email: 'b@x.io', role: 'member', status: 'active' }])); // reload
    render(<AdminPanel {...props()} />);
    fireEvent.click(await screen.findByRole('button', { name: /activate/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/users/u2',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'active' }) })));
  });

  it('does not offer self-disable for the current admin', async () => {
    apiFetch.mockResolvedValueOnce(json(200, [{ id: 'me', email: 'me@x.io', role: 'admin', status: 'active' }]));
    render(<AdminPanel {...props()} />);
    await screen.findByText('me@x.io');
    expect(screen.queryByRole('button', { name: /disable/i })).toBeNull();
  });
});
