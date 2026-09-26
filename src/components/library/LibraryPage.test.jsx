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

// d1: my own upload — full control (remove, retag, file into a project).
// d2: shared with me — it's my entry too (I can retag and remove my own
// copy), but I never uploaded it, so I can't file it into a project.
// d3: not in my library at all — I only see it because it's in a project I
// can see. Nothing here is mine to change.
const docs = [
  {
    doc_id: 'd1', file_name: 'Owned.pdf', state: 'indexed', tags: ['x'],
    projects: [], in_library: true, added_via: 'upload', shared_by: null,
  },
  {
    doc_id: 'd2', file_name: 'Teammate.pdf', state: 'indexed', tags: [],
    projects: [{ id: 'p1', name: 'Project A' }, { id: 'p2', name: 'Project B' }],
    in_library: true, added_via: 'shared', shared_by: { id: 'u2', name: 'Ann' },
  },
  {
    doc_id: 'd3', file_name: 'ViaProject.pdf', state: 'indexed', tags: [],
    projects: [{ id: 'p1', name: 'Project A' }],
    in_library: false, added_via: null, shared_by: null,
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

  it('fetches docs and projects on mount and renders library and via-project rows', async () => {
    mount();
    expect(await screen.findByText('Owned.pdf')).toBeInTheDocument();
    expect(screen.getByText('Teammate.pdf')).toBeInTheDocument();
    expect(screen.getByText('ViaProject.pdf')).toBeInTheDocument();
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

  it('gives an uploaded library row a remove control, a tag editor and a + project select', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    expect(within(row).getByRole('button', { name: 'Remove Owned.pdf from my library' })).toBeInTheDocument();
    expect(within(row).getByLabelText(/^add tag to owned\.pdf$/i)).toBeInTheDocument();
    expect(within(row).getByLabelText(/add owned\.pdf to project/i)).toBeInTheDocument();
    expect(within(row).queryByText(/shared by/i)).toBeNull();
    expect(within(row).queryByText(/via project/i)).toBeNull();
  });

  it('gives a shared library row a "shared by" badge, a remove control and a tag editor, but no + project select', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByText(/shared by ann/i)).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: 'Remove Teammate.pdf from my library' })).toBeInTheDocument();
    expect(within(row).getByLabelText(/^add tag to teammate\.pdf$/i)).toBeInTheDocument();
    expect(within(row).queryByLabelText(/add teammate\.pdf to project/i)).toBeNull();
  });

  it('never shows the + project select on a shared row, even when addable projects exist', async () => {
    const localDocs = [
      {
        doc_id: 'd2', file_name: 'Teammate.pdf', state: 'indexed', tags: [],
        projects: [], in_library: true, added_via: 'shared', shared_by: { id: 'u2', name: 'Ann' },
      },
    ];
    apiFetch.mockImplementation(async (host, port, path) => {
      if (path.startsWith('/v1/docs')) return json(200, localDocs);
      if (path === '/v1/projects') return json(200, projects);
      return json(404, {});
    });
    render(
      <LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} darkMode={false} effectiveIsMobile={false} />
    );
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    // Both p1 and p2 are unlinked and addable — if the select only checked
    // `addable.length > 0` it would render here. It must not: a shared entry
    // was never uploaded, so its holder can't file it anywhere.
    expect(within(row).queryByLabelText(/add teammate\.pdf to project/i)).toBeNull();
  });

  it('gives a via-project-only row a "via project" badge and no remove control or tag editor', async () => {
    mount();
    await screen.findByText('ViaProject.pdf');
    const row = screen.getByTestId('doc-row-d3');
    expect(within(row).getByText(/via project/i)).toBeInTheDocument();
    expect(within(row).queryByText(/shared by/i)).toBeNull();
    expect(within(row).queryByRole('button', { name: /remove viaproject\.pdf from my library/i })).toBeNull();
    expect(within(row).queryByLabelText(/add tag to viaproject\.pdf/i)).toBeNull();
  });

  it('PATCHes tags when a library holder adds a tag', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    fireEvent.change(within(row).getByLabelText(/^add tag to owned\.pdf$/i), { target: { value: 'y' } });
    fireEvent.click(within(row).getByLabelText(/confirm add tag/i));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs/d1', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ tags: ['x', 'y'] }),
    })));
  });

  it('renders one chip per linked project', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByText('Project A')).toBeInTheDocument();
    expect(within(row).getByText('Project B')).toBeInTheDocument();
  });

  it('PUTs a link when an uploader picks a project from the add select', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    fireEvent.change(within(row).getByLabelText(/add owned\.pdf to project/i), { target: { value: 'p2' } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p2/docs/d1', { method: 'PUT' }));
  });

  it('shows × on a chip only for the chip of a project the caller owns', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByLabelText(/remove teammate\.pdf from project a/i)).toBeInTheDocument();
    expect(within(row).queryByLabelText(/remove teammate\.pdf from project b/i)).toBeNull();
  });

  it('shows × only for owned-project chips even on the uploader\'s own row', async () => {
    const localDocs = [
      {
        doc_id: 'd1', file_name: 'Owned.pdf', state: 'indexed', tags: [],
        projects: [{ id: 'p1', name: 'Project A' }, { id: 'p2', name: 'Project B' }],
        in_library: true, added_via: 'upload', shared_by: null,
      },
    ];
    apiFetch.mockImplementation(async (host, port, path) => {
      if (path.startsWith('/v1/docs')) return json(200, localDocs);
      if (path === '/v1/projects') return json(200, projects);
      return json(404, {});
    });
    render(
      <LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} darkMode={false} effectiveIsMobile={false} />
    );
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    // p1 is caller-owned, p2 isn't — uploading the doc doesn't grant a say
    // over a project the caller doesn't own.
    expect(within(row).getByLabelText(/remove owned\.pdf from project a/i)).toBeInTheDocument();
    expect(within(row).queryByLabelText(/remove owned\.pdf from project b/i)).toBeNull();
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

  it('confirming a removal explains that others keep their copies and DELETEs only the caller\'s entry', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    fireEvent.click(within(row).getByRole('button', { name: 'Remove Owned.pdf from my library' }));
    expect(within(row).getByText(/people and projects that have it keep their copies/i)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: /confirm remove/i }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/docs/d1', { method: 'DELETE' }));
  });

  it('shows the describeRefusal notice, not "HTTP 404", when an unlink is refused', async () => {
    const showToast = vi.fn();
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/projects/p1/docs/d2' && opts?.method === 'DELETE') {
        return { ok: false, status: 404, json: async () => ({ detail: { error: 'not_found', message: 'nope' } }) };
      }
      if (path.startsWith('/v1/docs')) return json(200, docs);
      if (path === '/v1/projects') return json(200, projects);
      return json(404, {});
    });
    render(
      <LibraryPage theme={theme} apiHost="" apiPort="" showToast={showToast} darkMode={false} effectiveIsMobile={false} />
    );
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    fireEvent.click(within(row).getByLabelText(/remove teammate\.pdf from project a/i));

    await waitFor(() => expect(showToast).toHaveBeenCalled());
    const [message] = showToast.mock.calls[0];
    expect(message).not.toMatch(/HTTP 404/);
    expect(message).toMatch(/doesn't exist or you don't have access/i);
  });
});
