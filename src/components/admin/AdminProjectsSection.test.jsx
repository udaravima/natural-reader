import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AdminProjectsSection } from './AdminProjectsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const owned = { id: 'p1', name: 'Owned', created_at: '2026-10-01', member_count: 2, doc_count: 3, owners: [{ id: 'o', name: 'Olu' }], ownerless: false };
const orphan = { id: 'p2', name: 'Orphan', created_at: '2026-10-02', member_count: 0, doc_count: 1, owners: [], ownerless: true };

function mount() {
  apiFetch.mockImplementation(async (h, p, path, opts = {}) => {
    if (path === '/v1/admin/projects') return json(200, [orphan, owned]);
    if (path === '/v1/admin/projects?ownerless=true') return json(200, [orphan]);
    if (opts.method === 'PUT' && path === '/v1/projects/p2/members/admin1') return json(200, {});
    return json(404, {});
  });
  const props = { theme, apiHost: '', apiPort: '', currentUserId: 'admin1', showToast: vi.fn(), onOpenProject: vi.fn() };
  render(<AdminProjectsSection {...props} />);
  return props;
}

describe('AdminProjectsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists every project with owners and counts, and opens one', async () => {
    const props = mount();
    expect(await screen.findByText('Olu')).toBeTruthy();
    expect(screen.getByText('No owner')).toBeTruthy();
    expect(screen.getByText('2 members · 3 documents')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open Owned' }));
    expect(props.onOpenProject).toHaveBeenCalledWith('p1');
  });

  it('filters to ownerless projects', async () => {
    mount();
    await screen.findByText('Olu');
    fireEvent.click(screen.getByLabelText('Ownerless only'));
    await waitFor(() => expect(screen.queryByText('Olu')).toBeNull());
    expect(screen.getByText('Orphan')).toBeTruthy();
  });

  it('recovers an ownerless project by adding me as Owner', async () => {
    const props = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Add me as Owner of Orphan' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith("You're now an Owner of Orphan.", 4000));
    const put = apiFetch.mock.calls.find(([, , , o]) => o?.method === 'PUT');
    expect(JSON.parse(put[3].body)).toEqual({ role: 'owner' });
    expect(screen.queryByRole('button', { name: 'Add me as Owner of Owned' })).toBeNull();
  });
});
