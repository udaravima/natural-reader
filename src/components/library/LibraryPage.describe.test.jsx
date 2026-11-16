import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import LibraryPage from './LibraryPage';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const row = (over) => ({ state: 'indexed', tags: [], projects: [], in_library: true, added_via: 'upload', shared_by: null,
  description: null, generated: false, suggested_tags: [], describing: false, ...over });

function mount(docs, { helper = true } = {}) {
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path === '/v1/settings/helper') return json(200, { on: helper });
    if (opts?.method === 'POST' && path.endsWith('/describe')) return json(202, { describing: true });
    if (path.startsWith('/v1/docs')) return json(200, docs);
    if (path === '/v1/projects') return json(200, []);
    return json(404, {});
  });
  render(<LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
  fireEvent.click(screen.getByRole('tab', { name: 'Documents' }));
}

const calls = (method) => apiFetch.mock.calls.filter(([, , , o]) => o?.method === method).map(([, , path, o]) => [path, o.body]);

describe('LibraryPage — tags and descriptions (helper spec §5.4)', () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the helper's description, and its other tags as suggestions one tap keeps", async () => {
    mount([row({ doc_id: 'd1', file_name: 'Owned.pdf', tags: ['finance'], description: 'Yearly results.', generated: true,
      suggested_tags: ['risk'] })]);
    const r = await screen.findByTestId('doc-row-d1');
    expect(within(r).getByText('Yearly results.')).toBeTruthy();
    fireEvent.click(within(r).getByRole('button', { name: 'Keep the suggested tag risk' }));
    await waitFor(() => expect(calls('PATCH')).toEqual([['/v1/docs/d1', JSON.stringify({ tags: ['finance', 'risk'] })]]));
  });

  it('shows suggestions on a row only through a project, but none to keep', async () => {
    mount([row({ doc_id: 'd3', file_name: 'ViaProject.pdf', in_library: false, added_via: null, generated: true,
      suggested_tags: ['risk'], description: 'From a project.' })]);
    const r = await screen.findByTestId('doc-row-d3');
    expect(within(r).getByText('From a project.')).toBeTruthy();
    expect(within(r).getByText('risk')).toBeTruthy();
    expect(within(r).queryByRole('button', { name: 'Keep the suggested tag risk' })).toBeNull();
  });

  it('offers Generate on an indexed row of mine never described, with the helper on', async () => {
    mount([row({ doc_id: 'd1', file_name: 'Owned.pdf' })]);
    const r = await screen.findByTestId('doc-row-d1');
    fireEvent.click(await within(r).findByRole('button', { name: 'Generate tags for Owned.pdf' }));
    await waitFor(() => expect(calls('POST')).toEqual([['/v1/docs/d1/describe', undefined]]));
  });

  it('offers no Generate once described, while describing, without the helper, or on a row not mine', async () => {
    mount([
      row({ doc_id: 'd1', file_name: 'Done.pdf', generated: true }),
      row({ doc_id: 'd2', file_name: 'Busy.pdf', describing: true }),
      row({ doc_id: 'd3', file_name: 'Theirs.pdf', in_library: false, added_via: null }),
      row({ doc_id: 'd4', file_name: 'Indexing.pdf', state: 'indexing' }),
    ]);
    await screen.findByTestId('doc-row-d1');
    await waitFor(() => expect(apiFetch.mock.calls.some(([, , p]) => p === '/v1/settings/helper')).toBe(true));
    expect(screen.queryByRole('button', { name: /^Generate tags for/ })).toBeNull();
    expect(within(screen.getByTestId('doc-row-d2')).getByText('Writing tags…')).toBeTruthy();
  });

  it('offers no Generate with the helper off', async () => {
    mount([row({ doc_id: 'd1', file_name: 'Owned.pdf' })], { helper: false });
    await screen.findByTestId('doc-row-d1');
    await waitFor(() => expect(apiFetch.mock.calls.some(([, , p]) => p === '/v1/settings/helper')).toBe(true));
    expect(screen.queryByRole('button', { name: 'Generate tags for Owned.pdf' })).toBeNull();
  });
});
