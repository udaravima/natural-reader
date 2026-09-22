import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AdminConsole } from './AdminConsole';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const existing = {
  id: 'u1', email: 'a@x.io', display_name: null, role: 'member', status: 'active',
  oidc_iss: 'https://kc', oidc_sub: 'sub-1', capabilities: [],
  inference_daily_token_budget: null, created_at: '2026-09-17T00:00:00Z',
};

describe('AdminConsole enroll — in-flight feedback', () => {
  beforeEach(() => vi.clearAllMocks());

  it('disables the Enroll button and shows an Enrolling state while the create request is in flight', async () => {
    let resolveEnroll;
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/admin/users' && opts?.method === 'POST') {
        return new Promise((r) => {
          resolveEnroll = () => r(json(201, { ...existing, id: 'u2', email: 'new@x.io', oidc_sub: null }));
        });
      }
      if (path === '/v1/admin/users') return json(200, [existing]);
      if (path.startsWith('/v1/admin/inference/usage')) return json(200, []);
      if (path === '/v1/admin/inference/config') return json(200, {});
      return json(404, {});
    });
    render(
      <AdminConsole theme={theme} apiHost="" apiPort="" currentUserId="me" onBack={vi.fn()} showToast={vi.fn()} />
    );
    await screen.findByText('a@x.io');

    fireEvent.change(screen.getByLabelText('Enroll email'), { target: { value: 'new@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));

    const busy = await screen.findByRole('button', { name: /enrolling/i });
    expect(busy).toBeDisabled();

    resolveEnroll();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^enroll$/i })).toBeEnabled());
  });
});
