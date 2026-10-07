import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ProjectPage from './ProjectPage';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: true,
  remove_docs: false, delete: false, leave: true, ...over });
const proj = (over = {}) => ({ id: 'p1', name: 'Q3 Audit', description: 'Evidence', my_role: 'contributor',
  member_count: 2, doc_count: 0, created_by: null, can: can(), ...over });

function mount(routes) {
  apiFetch.mockImplementation(async (h, p, path, opts = {}) => {
    const key = `${opts.method || 'GET'} ${path}`;
    const hit = routes[key];
    if (hit) return typeof hit === 'function' ? hit(opts) : hit;
    if (path.startsWith('/v1/docs')) return json(200, []);
    return json(404, {});
  });
  const props = { theme, apiHost: '', apiPort: '', projectId: 'p1', currentUserId: 'me',
    showToast: vi.fn(), onBack: vi.fn(), onChanged: vi.fn(), onOpenDoc: vi.fn() };
  render(<ProjectPage {...props} />);
  return props;
}

describe('ProjectPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the name, my role, and only the controls my role allows', async () => {
    mount({ 'GET /v1/projects/p1': json(200, proj()) });
    expect(await screen.findByRole('heading', { name: /Q3 Audit/ })).toBeTruthy();
    expect(screen.getByText('Contributor')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit project' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete project' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Leave project' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Documents', selected: true })).toBeTruthy();
  });

  it('leaving asks first, removes me, and goes back', async () => {
    const props = mount({ 'GET /v1/projects/p1': json(200, proj()), 'DELETE /v1/projects/p1/members/me': json(204, null) });
    fireEvent.click(await screen.findByRole('button', { name: 'Leave project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm leave' }));
    await waitFor(() => expect(props.onBack).toHaveBeenCalled());
    expect(props.onChanged).toHaveBeenCalled();
  });

  it('a Maintainer edits the name and description', async () => {
    const saved = proj({ name: 'Q4 Audit', my_role: 'maintainer', can: can({ edit: true }) });
    mount({ 'GET /v1/projects/p1': json(200, proj({ my_role: 'maintainer', can: can({ edit: true }) })),
      'PATCH /v1/projects/p1': json(200, saved) });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Q4 Audit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('heading', { name: /Q4 Audit/ })).toBeTruthy();
    const patch = apiFetch.mock.calls.find(([, , , o]) => o?.method === 'PATCH');
    expect(JSON.parse(patch[3].body)).toEqual({ name: 'Q4 Audit', description: 'Evidence' });
  });

  it('reloads after a refusal so stale controls disappear', async () => {
    let n = 0;
    mount({
      'GET /v1/projects/p1': () => json(200, n++ === 0
        ? proj({ my_role: 'maintainer', can: can({ edit: true }) })
        : proj({ my_role: 'reader', can: can({ file_docs: false }) })),
      'PATCH /v1/projects/p1': json(403, { detail: { error: 'insufficient_role', required: 'maintainer' } }),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('Reader')).toBeTruthy());
    expect(screen.queryByRole('button', { name: 'Edit project' })).toBeNull();
  });

  it('a project I can no longer see sends me back with the notice', async () => {
    const props = mount({ 'GET /v1/projects/p1': json(404, { detail: { error: 'not_found',
      message: "This project doesn't exist or you don't have access." } }) });
    await waitFor(() => expect(props.onBack).toHaveBeenCalled());
    expect(props.showToast).toHaveBeenCalledWith("This project doesn't exist or you don't have access.", 5000);
  });

  it('an Owner deletes the project after confirming', async () => {
    const props = mount({ 'GET /v1/projects/p1': json(200, proj({ my_role: 'owner', can: can({ delete: true }) })),
      'DELETE /v1/projects/p1': json(204, null) });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete project' }));
    expect(screen.getByText(/a document only this project held is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(props.onBack).toHaveBeenCalled());
  });
});
