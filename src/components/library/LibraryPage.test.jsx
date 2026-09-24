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
    doc_id: 'd1', file_name: 'Owned.pdf', state: 'indexed', tags: ['x'],
    projects: [], owner_user_id: 'me', is_owner: true,
  },
  {
    doc_id: 'd2', file_name: 'Teammate.pdf', state: 'indexed', tags: [],
    projects: [{ id: 'p1', name: 'Project A' }, { id: 'p2', name: 'Project B' }],
    owner_user_id: 'other', is_owner: false,
  },
];

const projects = [
  { id: 'p1', owner_user_id: 'me', name: 'Project A', description: null, is_owner: true },
  { id: 'p2', owner_user_id: 'other', name: 'Project B', description: null, is_owner: false },
];

function mount({ showToast = vi.fn() } = {}) {
  apiFetch.mockImplementation(async (host, port, path) => {
    if (path.startsWith('/v1/docs')) return json(200, docs);
    if (path === '/v1/projects') return json(200, projects);
    if (path.startsWith('/v1/projects/')) return json(204, {});
    return json(404, {});
  });
  render(
    <LibraryPage theme={theme} apiHost="" apiPort="" showToast={showToast} darkMode={false} effectiveIsMobile={false} />
  );
}

const docsCalls = () => apiFetch.mock.calls.filter(([, , path]) => path.startsWith('/v1/docs'));

describe('LibraryPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('fetches docs and projects on mount and renders both an owned and a shared doc', async () => {
    mount();
    expect(await screen.findByText('Owned.pdf')).toBeInTheDocument();
    expect(screen.getByText('Teammate.pdf')).toBeInTheDocument();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects'));
    expect(docsCalls().length).toBeGreaterThan(0);
  });

  it('issues a q= search request after typing in the search box', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    fireEvent.change(screen.getByLabelText(/search documents/i), { target: { value: 'foo' } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs?q=foo'));
  });

  it('issues a project_id= request when a project filter is selected', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    fireEvent.change(screen.getByLabelText(/filter by project/i), { target: { value: 'p1' } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs?project_id=p1'));
  });

  it('shows a shared indicator and no delete affordance on a shared (non-owned) row', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const sharedRow = screen.getByTestId('doc-row-d2');
    expect(within(sharedRow).getByText(/shared/i)).toBeInTheDocument();
    expect(within(sharedRow).queryByRole('button', { name: /delete/i })).toBeNull();
  });

  it('gives the owned row a delete affordance and no shared indicator', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const ownedRow = screen.getByTestId('doc-row-d1');
    expect(within(ownedRow).getByRole('button', { name: /delete/i })).toBeInTheDocument();
    expect(within(ownedRow).queryByText(/shared/i)).toBeNull();
  });

  it('PATCHes tags when an owner adds a tag', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const ownedRow = screen.getByTestId('doc-row-d1');
    fireEvent.change(within(ownedRow).getByLabelText(/^add tag to owned\.pdf$/i), { target: { value: 'y' } });
    fireEvent.click(within(ownedRow).getByLabelText(/confirm add tag/i));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs/d1', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ tags: ['x', 'y'] }),
    })));
  });

  it('has no tag-edit or reassign affordance on a shared row', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const sharedRow = screen.getByTestId('doc-row-d2');
    expect(within(sharedRow).queryByLabelText(/add tag/i)).toBeNull();
    expect(within(sharedRow).queryByLabelText(/add teammate\.pdf to project/i)).toBeNull();
  });

  it('renders one chip per linked project', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByText('Project A')).toBeInTheDocument();
    expect(within(row).getByText('Project B')).toBeInTheDocument();
  });

  it('PUTs a link when the owner picks a project from the add select', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    fireEvent.change(within(row).getByLabelText(/add owned\.pdf to project/i), { target: { value: 'p2' } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p2/docs/d1', { method: 'PUT' }));
  });

  it('shows × on a shared doc only for the chip of a project the caller owns', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByLabelText(/remove teammate\.pdf from project a/i)).toBeInTheDocument();
    expect(within(row).queryByLabelText(/remove teammate\.pdf from project b/i)).toBeNull();
  });

  it('DELETEs the link and reloads the list when × is clicked', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const before = docsCalls().length;
    fireEvent.click(within(screen.getByTestId('doc-row-d2')).getByLabelText(/remove teammate\.pdf from project a/i));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p1/docs/d2', { method: 'DELETE' }));
    await waitFor(() => expect(docsCalls().length).toBeGreaterThan(before));
  });

  it('guards against a double-click on × — only one DELETE fires while the link change is in flight', async () => {
    let resolveDelete;
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/projects/p1/docs/d2' && opts?.method === 'DELETE') {
        return new Promise((resolve) => {
          resolveDelete = () => resolve(json(204, {}));
        });
      }
      if (path.startsWith('/v1/docs')) return json(200, docs);
      if (path === '/v1/projects') return json(200, projects);
      if (path.startsWith('/v1/projects/')) return json(204, {});
      return json(404, {});
    });
    render(
      <LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} darkMode={false} effectiveIsMobile={false} />
    );
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    const removeBtn = within(row).getByLabelText(/remove teammate\.pdf from project a/i);

    fireEvent.click(removeBtn);
    fireEvent.click(removeBtn);

    const deleteCalls = () => apiFetch.mock.calls.filter(
      ([, , path, opts]) => path === '/v1/projects/p1/docs/d2' && opts?.method === 'DELETE'
    );
    // Give the second click's event handler a chance to run before asserting
    // only one request actually went out.
    await waitFor(() => expect(deleteCalls().length).toBe(1));

    const before = docsCalls().length;
    resolveDelete();
    await waitFor(() => expect(docsCalls().length).toBeGreaterThan(before));
  });

  it('offers only unlinked projects in the add select', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const select = within(screen.getByTestId('doc-row-d1')).getByLabelText(/add owned\.pdf to project/i);
    const values = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    expect(values).toEqual(['', 'p1', 'p2']);
  });
});
