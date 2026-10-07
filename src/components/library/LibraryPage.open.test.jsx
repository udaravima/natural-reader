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

// Every row the Library lists is one the caller can read — own upload,
// shared with them, or reached through a project — so every row opens.
const docs = [
  { doc_id: 'd1', file_name: 'Owned.pdf', state: 'indexed', tags: [], projects: [],
    in_library: true, added_via: 'upload', shared_by: null },
  { doc_id: 'd2', file_name: 'Teammate.md', state: 'indexed', tags: [], projects: [],
    in_library: true, added_via: 'shared', shared_by: { id: 'u2', name: 'Ann' } },
  { doc_id: 'd3', file_name: 'ViaProject.txt', state: 'stored', tags: [],
    projects: [{ id: 'p1', name: 'Project A' }], in_library: false, added_via: null, shared_by: null },
];

function mount(onOpen) {
  apiFetch.mockImplementation(async (host, port, path) => {
    if (path.startsWith('/v1/docs')) return json(200, docs);
    if (path === '/v1/projects') return json(200, []);
    return json(404, {});
  });
  render(<LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} onOpen={onOpen} />);
}

describe('LibraryPage — Open', () => {
  beforeEach(() => vi.clearAllMocks());

  it.each(['d1', 'd2', 'd3'])('row %s has an Open button that hands the row to onOpen', async (id) => {
    const onOpen = vi.fn(async () => true);
    mount(onOpen);
    const doc = docs.find((d) => d.doc_id === id);
    await screen.findByText(doc.file_name);
    const row = screen.getByTestId(`doc-row-${id}`);
    fireEvent.click(within(row).getByRole('button', { name: `Open ${doc.file_name}` }));
    expect(onOpen).toHaveBeenCalledWith(doc);
  });

  it('the button is busy while opening, so a double-click opens once', async () => {
    let finish;
    const onOpen = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    mount(onOpen);
    await screen.findByText('Owned.pdf');
    const button = within(screen.getByTestId('doc-row-d1')).getByRole('button', { name: 'Open Owned.pdf' });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledTimes(1);
    finish(false);
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it('shows no Open button when there is nothing to open into', async () => {
    mount(undefined);
    await screen.findByText('Owned.pdf');
    expect(screen.queryByRole('button', { name: /^Open / })).toBeNull();
  });
});
