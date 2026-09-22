import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import LibraryPage from './LibraryPage';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = {
  bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

const docs = [
  {
    doc_id: 'd1', file_name: 'Owned.pdf', state: 'indexed', tags: [],
    project_id: null, project_name: null, owner_user_id: 'me', is_owner: true,
  },
];
const projects = [];

describe('LibraryPage delete — in-flight feedback', () => {
  beforeEach(() => vi.clearAllMocks());

  it('disables the confirm button and shows a Deleting state while the DELETE is in flight', async () => {
    let resolveDelete;
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path.startsWith('/v1/docs/') && opts?.method === 'DELETE') {
        return new Promise((r) => { resolveDelete = () => r(json(200, {})); });
      }
      if (path.startsWith('/v1/docs')) return json(200, docs);
      if (path === '/v1/projects') return json(200, projects);
      return json(404, {});
    });
    render(
      <LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} darkMode={false} effectiveIsMobile={false} />
    );
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');

    fireEvent.click(within(row).getByRole('button', { name: 'Delete Owned.pdf' }));
    fireEvent.click(within(row).getByRole('button', { name: /confirm delete/i }));

    // While the request is pending the button reports a busy state and can't be
    // clicked again — no more silent "did that even register?" clicks.
    const busy = await within(row).findByRole('button', { name: /deleting/i });
    expect(busy).toBeDisabled();

    resolveDelete();
    await waitFor(() =>
      expect(within(row).queryByRole('button', { name: /deleting/i })).toBeNull());
  });
});
