import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ProjectDocsTab from './ProjectDocsTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: false,
  remove_docs: false, delete: false, leave: true, ...over });
const project = (over = {}) => ({ id: 'p1', name: 'Q3', my_role: 'contributor', can: can({ file_docs: true }), ...over });
const placed = { doc_id: 'd1', file_name: 'Placed.pdf', state: 'indexed', projects: [{ id: 'p1', name: 'Q3' }], added_via: null };
const mineFree = { doc_id: 'd2', file_name: 'Mine.pdf', state: 'indexed', projects: [], added_via: 'upload' };
const sharedToMe = { doc_id: 'd3', file_name: 'Shared.pdf', state: 'indexed', projects: [], added_via: 'shared' };

function api(over = {}) {
  return {
    projectDocs: vi.fn(async () => [placed]),
    myDocs: vi.fn(async () => [placed, mineFree, sharedToMe]),
    fileDoc: vi.fn(async () => null),
    unfileDoc: vi.fn(async () => null),
    ...over,
  };
}

function mount(p, a = api(), extra = {}) {
  const props = { theme, api: a, project: p, showToast: vi.fn(), onRefused: vi.fn(), onChanged: vi.fn(), ...extra };
  render(<ProjectDocsTab {...props} />);
  return props;
}

describe('ProjectDocsTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists the project documents and offers only my own unfiled uploads to file', async () => {
    const a = api();
    const props = mount(project(), a);
    expect(await screen.findByText('Placed.pdf')).toBeTruthy();
    const select = screen.getByLabelText('File a document into this project');
    expect([...select.options].map((o) => o.textContent)).toEqual(['File a document…', 'Mine.pdf']);
    fireEvent.change(select, { target: { value: 'd2' } });
    await waitFor(() => expect(a.fileDoc).toHaveBeenCalledWith('p1', 'd2'));
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });

  it('has no file or remove controls for a Reader', async () => {
    mount(project({ my_role: 'reader', can: can() }));
    await screen.findByText('Placed.pdf');
    expect(screen.queryByLabelText('File a document into this project')).toBeNull();
    expect(screen.queryByLabelText('Remove Placed.pdf from Q3')).toBeNull();
  });

  it('asks before a Maintainer removes a document, then removes it', async () => {
    const a = api();
    mount(project({ my_role: 'maintainer', can: can({ file_docs: true, remove_docs: true }) }), a);
    fireEvent.click(await screen.findByLabelText('Remove Placed.pdf from Q3'));
    expect(screen.getByText(/If nobody else has it in their library, it is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(a.unfileDoc).toHaveBeenCalledWith('p1', 'd1'));
  });

  it('shows the notice and asks the page to reload when the server refuses', async () => {
    const a = api({ unfileDoc: vi.fn(async () => { throw new Error('Only Maintainers or Owners can do that.'); }) });
    const props = mount(project({ my_role: 'maintainer', can: can({ remove_docs: true }) }), a);
    fireEvent.click(await screen.findByLabelText('Remove Placed.pdf from Q3'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Only Maintainers or Owners can do that.', 5000));
    expect(props.onRefused).toHaveBeenCalled();
  });

  it('admin non-member sees the members-only note instead of documents', async () => {
    const a = api();
    mount(project({ my_role: null, can: can({ edit: true, manage_members: true, manage_owners: true, delete: true, leave: false }) }), a);
    expect(screen.getByText("Only members can see this project's documents.")).toBeTruthy();
    expect(a.projectDocs).not.toHaveBeenCalled();
  });
});
