import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import { AdminConsole } from './AdminConsole';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const noContent = () => ({ ok: true, status: 204 });

const user = (over = {}) => ({
  id: 'u1', email: 'a@x.io', display_name: null, role: 'member', status: 'active',
  oidc_iss: 'https://kc', oidc_sub: 'sub-1', capabilities: [],
  inference_daily_token_budget: null, created_at: '2026-09-17T00:00:00Z',
  project_limit: null, username: null, first_name: null, last_name: null,
  ...over,
});

// Mounts the console with a mutable user list so PATCH/POST/DELETE handlers
// can mutate it and the reload reflects the change, like the real backend.
// enrollResponse(created) lets a test opt into the current
// { user, onboarding, temp_password? } POST contract; by default the mock
// returns the raw created-user row (the shape older/simpler tests assert on).
// extra(path, opts) answers routes of its own first (undefined = fall through).
// section is the tab opened after mounting (the console opens on Overview); null stays on Overview.
function mount({ users, usage = [], deployment = { settings: { timezone: { value: 'UTC', source: 'default', env: 'TZ', fallback: 'UTC', fallback_source: 'default' } }, info: [] }, showToast = vi.fn(), currentUserId = 'me', enrollResponse, extra, section = 'People' } = {}) {
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    const answered = extra?.(path, opts);   // a test's own routes (chat options, the assistant) win
    if (answered) return answered;
    if (path === '/v1/admin/users' && opts?.method === 'POST') {
      const body = JSON.parse(opts.body);
      if (users.some((u) => u.email === body.email)) return json(409, { detail: 'email already exists' });
      const created = user({ id: `u${users.length + 1}`, ...body, oidc_sub: null });
      users.push(created);
      return json(201, enrollResponse ? enrollResponse(created) : created);
    }
    if (path.startsWith('/v1/admin/users/') && opts?.method === 'PATCH') return json(200, {});
    if (path.startsWith('/v1/admin/users/') && opts?.method === 'DELETE') {
      const id = path.split('/').pop();
      const i = users.findIndex((u) => u.id === id);
      if (i >= 0) users.splice(i, 1);
      return noContent();
    }
    if (path === '/v1/admin/users') return json(200, users);
    if (path.startsWith('/v1/admin/inference/usage')) return json(200, usage);
    if (path === '/v1/admin/settings/deployment') return json(200, deployment);
    if (path.startsWith('/v1/admin/projects')) return json(200, []);
    if (path === '/v1/admin/overview') return json(200, { checks: [] });
    return json(404, {});
  });
  render(
    <AdminConsole
      theme={theme} apiHost="" apiPort="" currentUserId={currentUserId}
      onBack={vi.fn()} showToast={showToast}
    />
  );
  if (section) openSection(section);
}

function openSection(name) { fireEvent.click(screen.getByRole('tab', { name })); }

const calls = (method) =>
  apiFetch.mock.calls.filter(([, , , opts]) => opts?.method === method);
const lastBody = (method) => JSON.parse(calls(method).at(-1)[3].body);

describe('AdminConsole — users', () => {
  // mount() is called inside each test and opens People (the console itself opens on Overview).
  it('sets a per-user project limit, empty meaning the default', async () => {
    mount({ users: [user()] });
    fireEvent.change(await screen.findByLabelText('Project limit for a@x.io'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set project limit for a@x.io' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(1));
    expect(lastBody('PATCH')).toEqual({ project_limit: 5 });
    fireEvent.change(screen.getByLabelText('Project limit for a@x.io'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set project limit for a@x.io' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(2));
    expect(lastBody('PATCH')).toEqual({ project_limit: null });
  });

  it('captions the budget and project-limit fields visibly, not only for screen readers', async () => {
    mount({ users: [user()] });
    await screen.findByLabelText('Project limit for a@x.io');
    expect(screen.getByText('tokens/day')).toBeVisible();
    expect(screen.getByText('projects')).toBeVisible();
  });

  it('enrolls with username, first and last name', async () => {
    mount({ users: [user()] });
    fireEvent.change(await screen.findByLabelText('Enroll email'), { target: { value: 'n@example.com' } });
    fireEvent.change(screen.getByLabelText('Enroll username'), { target: { value: 'newbie' } });
    fireEvent.change(screen.getByLabelText('Enroll first name'), { target: { value: 'New' } });
    fireEvent.change(screen.getByLabelText('Enroll last name'), { target: { value: 'Person' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enroll' }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(lastBody('POST')).toMatchObject({ email: 'n@example.com', username: 'newbie', first_name: 'New', last_name: 'Person' });
  });

  beforeEach(() => vi.clearAllMocks());

  it('renders rows and the awaiting-first-login badge for unlinked rows', async () => {
    mount({ users: [user(), user({ id: 'u2', email: 'b@x.io', oidc_sub: null, status: 'pending' })] });
    expect(await screen.findByText('a@x.io')).toBeInTheDocument();
    expect(screen.getByText('awaiting first login')).toBeInTheDocument();
    expect(screen.getByText(/member\/pending/)).toBeInTheDocument();
    expect(within(screen.getByTestId('user-row-u2')).getByText(/2026-09-17/)).toBeInTheDocument();
  });

  it('PATCHes status without the budget field when untouched (exclude_unset)', async () => {
    mount({ users: [user({ status: 'pending' })] });
    fireEvent.click(await screen.findByRole('button', { name: /activate/i }));
    await waitFor(() => expect(calls('PATCH').length).toBeGreaterThan(0));
    expect(lastBody('PATCH')).toEqual({ status: 'active' });
  });

  it('sends 0 as unlimited and empty as an explicit null clear', async () => {
    mount({ users: [user()] });
    await screen.findByText('a@x.io');
    const input = screen.getByLabelText('Budget for a@x.io');
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(1));
    expect(lastBody('PATCH')).toEqual({ inference_daily_token_budget: 0 });
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(2));
    expect(lastBody('PATCH')).toEqual({ inference_daily_token_budget: null });
  });

  it('hides Disable and Delete, and locks the admin capability, on the self row', async () => {
    mount({ users: [user({ id: 'me', role: 'admin', capabilities: ['admin', 'reader', 'chat'] })] });
    await screen.findByText('a@x.io');
    const row = screen.getByTestId('user-row-me');
    expect(within(row).queryByRole('button', { name: /disable/i })).toBeNull();
    expect(within(row).queryByRole('button', { name: /delete/i })).toBeNull();
    // Self-lockout guard: the admin checkbox is checked (it reflects the
    // real capability) but disabled, so the admin can't strip their own
    // admin access from this row. reader/chat stay editable.
    const adminBox = within(row).getByRole('checkbox', { name: 'admin' });
    expect(adminBox).toBeChecked();
    expect(adminBox).toBeDisabled();
    expect(within(row).getByRole('checkbox', { name: 'reader' })).toBeEnabled();
    fireEvent.click(adminBox);
    expect(calls('PATCH').length).toBe(0);
  });

  it('PATCHes capabilities when a capability checkbox is toggled', async () => {
    const users = [user({ id: 'u2', email: 'b@x.io', capabilities: ['reader'] })];
    mount({ users });
    await screen.findByText('b@x.io');
    const row = screen.getByTestId('user-row-u2');
    fireEvent.click(within(row).getByRole('checkbox', { name: 'chat' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(1));
    expect(calls('PATCH')[0][2]).toBe('/v1/admin/users/u2');
    const caps = lastBody('PATCH').capabilities;
    expect(caps).toEqual(expect.arrayContaining(['reader', 'chat']));
    expect(caps).toHaveLength(2);
  });

  it('offers the docling capability on a person and when enrolling', async () => {
    const users = [user({ id: 'u2', email: 'b@x.io', capabilities: ['reader'] })];
    mount({ users });
    await screen.findByText('b@x.io');
    const row = screen.getByTestId('user-row-u2');
    fireEvent.click(within(row).getByRole('checkbox', { name: 'docling' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(1));
    expect(lastBody('PATCH').capabilities.sort()).toEqual(['docling', 'reader']);
    const form = screen.getByLabelText(/enroll email/i).closest('form');
    expect(within(form).getByRole('checkbox', { name: 'docling' })).not.toBeChecked();
  });

  it('includes selected capabilities in the enroll POST body', async () => {
    mount({ users: [user({ id: 'me', role: 'admin' })] });
    await screen.findByText('a@x.io');
    const form = screen.getByLabelText(/enroll email/i).closest('form');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'new3@x.io' } });
    fireEvent.click(within(form).getByRole('checkbox', { name: 'reader' }));
    fireEvent.click(within(form).getByRole('checkbox', { name: 'chat' }));
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(lastBody('POST').capabilities).toEqual(expect.arrayContaining(['reader', 'chat']));
  });

  it('shows the temp password once after enroll', async () => {
    mount({
      users: [user({ id: 'me', role: 'admin' })],
      enrollResponse: (created) => ({ user: created, onboarding: 'temp_password', temp_password: 'abc' }),
    });
    await screen.findByText('a@x.io');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'new@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(await screen.findByText(/abc/)).toBeInTheDocument();
    expect(screen.getByText(/share this once/i)).toBeInTheDocument();
  });

  it('shows an invite-emailed note, not a password, when onboarding is email', async () => {
    mount({
      users: [user({ id: 'me', role: 'admin' })],
      enrollResponse: (created) => ({ user: created, onboarding: 'email' }),
    });
    await screen.findByText('a@x.io');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'new2@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(await screen.findByText(/invite emailed/i)).toBeInTheDocument();
    expect(screen.queryByText(/temp password/i)).toBeNull();
  });

  it('delete confirm requires the typed email, then DELETEs and toasts', async () => {
    const showToast = vi.fn();
    const users = [user({ id: 'me', role: 'admin' }), user({ id: 'u2', email: 'b@x.io', oidc_sub: null })];
    mount({ users, showToast });
    await screen.findByText('b@x.io');
    const row = screen.getByTestId('user-row-u2');
    fireEvent.click(within(row).getByRole('button', { name: /delete/i }));
    const confirm = within(row).getByRole('button', { name: /confirm delete/i });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(row).getByLabelText(/confirm email/i), { target: { value: 'wrong@x.io' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(row).getByLabelText(/confirm email/i), { target: { value: 'b@x.io' } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(calls('DELETE').length).toBe(1));
    expect(calls('DELETE')[0][2]).toBe('/v1/admin/users/u2');
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('library and chat history'), expect.anything()
    ));
    const deleted = showToast.mock.calls.filter(([m]) => m.includes('library and chat history were removed'));
    expect(deleted).toHaveLength(1);
  });

  it('states what deleting a user does to their projects, and reloads the Projects section afterwards', async () => {
    const users = [user({ id: 'me', role: 'admin' }), user({ id: 'u2', email: 'b@x.io', oidc_sub: null })];
    mount({ users });
    await screen.findByText('b@x.io');
    const projectGets = () => apiFetch.mock.calls.filter(([, , path]) => path.startsWith('/v1/admin/projects')).length;
    expect(projectGets()).toBe(0);
    const row = screen.getByTestId('user-row-u2');
    fireEvent.click(within(row).getByRole('button', { name: /delete/i }));
    expect(within(row).getByText(/permanently deletes their library and chat history\. Projects they belong to stay; any they were the only Owner of become ownerless \(recover them under Projects\)\./)).toBeTruthy();
    fireEvent.change(within(row).getByLabelText(/confirm email/i), { target: { value: 'b@x.io' } });
    fireEvent.click(within(row).getByRole('button', { name: /confirm delete/i }));
    await waitFor(() => expect(calls('DELETE').length).toBe(1));
    openSection('Projects');
    await waitFor(() => expect(projectGets()).toBe(1));
  });

  it('shows one section at a time', async () => {
    mount({ users: [user()] });
    await screen.findByText('a@x.io');
    expect(screen.queryByRole('region', { name: 'Inference usage' })).toBeNull();
    openSection('Usage');
    expect(await screen.findByRole('region', { name: 'Inference usage' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Users' })).toBeNull();
  });

  it('enrolls a pre-provisioned user and shows the claim instruction', async () => {
    const users = [user({ id: 'me', role: 'admin' })];
    mount({ users });
    await screen.findByText('a@x.io');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'new@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(lastBody('POST')).toEqual({ email: 'new@x.io' });
    expect(await screen.findByText(/have the user log in with this exact verified email/i)).toBeInTheDocument();
    expect(await screen.findByText('awaiting first login')).toBeInTheDocument();
  });

  it('renders the duplicate-email 409 inline', async () => {
    const users = [user({ id: 'me', role: 'admin', email: 'me@x.io' })];
    mount({ users });
    await screen.findByText('me@x.io');
    fireEvent.change(screen.getByLabelText(/enroll email/i), { target: { value: 'me@x.io' } });
    fireEvent.click(screen.getByRole('button', { name: /^enroll$/i }));
    expect(await screen.findByText(/already taken/i)).toBeInTheDocument();
  });
});

describe('AdminConsole — overview', () => {
  beforeEach(() => vi.clearAllMocks());

  it('opens on Overview, and an Open link switches section', async () => {
    mount({
      users: [user()], section: null,
      extra: (path) => path === '/v1/admin/overview' ? json(200, { checks: [
        { id: 'docling', state: 'error', title: 'Docling', summary: 'Docling is not installed.', section: 'documents' },
      ] }) : undefined,
    });
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(await screen.findByRole('button', { name: 'Open Documents' }));
    expect(screen.getByRole('tab', { name: 'Documents' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'false');
  });
});

describe('AdminConsole — documents', () => {
  beforeEach(() => vi.clearAllMocks());

  const rule = (env, value) => ({ value, source: 'default', env, fallback: value, fallback_source: 'default' });
  const documents = { settings: {
    convert_by: rule('DOCLING_CONVERT_BY', 'readers'),
    presets: rule('DOCLING_PRESETS', ['fast', 'standard', 'accurate']),
    ocr: rule('DOCLING_ALLOW_OCR', true),
    picture_description: rule('DOCLING_ALLOW_PICTURE_DESCRIPTION', true),
    max_pages: rule('DOCLING_MAX_PAGES', 0),
  }, info: [] };

  it('asks before leaving Documents with an unsaved limit', async () => {
    mount({ users: [user()], section: null, extra: (path) => (path === '/v1/admin/settings/documents' ? json(200, documents) : undefined) });
    openSection('Documents');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Allow OCR' }));
    openSection('People');
    expect(screen.getByRole('alertdialog')).toHaveTextContent('You have unsaved changes in Documents.');
    expect(screen.getByRole('tab', { name: 'Documents' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('AdminConsole — usage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says days are counted in the deployment timezone', async () => {
    mount({ users: [user()], usage: [{ email: 'a@x.io', user_id: 'u1', day: '2026-09-17', prompt_tokens: 1, eval_tokens: 1, requests: 1, tokens: 2 }] });
    openSection('Usage');
    expect(await screen.findByRole('columnheader', { name: 'Day' })).toBeInTheDocument();
    expect(screen.getByText("Each day is counted in the deployment's timezone. Changing it doesn't move days already counted.")).toBeInTheDocument();
  });

  it('renders ISO day rows as-is', async () => {
    mount({
      users: [user()],
      usage: [{ email: 'a@x.io', user_id: 'u1', day: '2026-09-17', prompt_tokens: 10, eval_tokens: 5, requests: 2, tokens: 15 }],
    });
    openSection('Usage');
    expect(await screen.findByText('2026-09-17')).toBeInTheDocument();
    expect(screen.getByText('15')).toBeInTheDocument();
  });

  it('shows the empty state and re-fetches when the days selector changes', async () => {
    mount({ users: [user()], usage: [] });
    openSection('Usage');
    expect(await screen.findByText('No usage recorded yet.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/usage days/i), { target: { value: '30' } });
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/admin/inference/usage?days=30')
    );
  });
});

describe('AdminConsole — deployment', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists the providers from the server settings, never a key', async () => {
    mount({
      users: [user()],
      deployment: {
        settings: { timezone: { value: 'UTC', source: 'default', env: 'TZ', fallback: 'UTC', fallback_source: 'default' } },
        info: [
          { id: 'provider:cloud', label: 'Provider cloud', value: 'openai · https://llm.example.com/v1', env: 'INFERENCE_CLOUD_URL', source: 'env' },
          { id: 'provider_key:cloud', label: 'Provider cloud key', value: 'set', env: 'INFERENCE_CLOUD_API_KEY', source: 'env' },
        ],
      },
    });
    openSection('Deployment');
    expect(await screen.findByText('openai · https://llm.example.com/v1')).toBeInTheDocument();
    expect(screen.getByText('set')).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalledWith('', '', '/v1/admin/inference/config');
  });

  it('asks before leaving Deployment with an unsaved zone', async () => {
    mount({ users: [user()], section: null });
    openSection('Deployment');
    fireEvent.change(await screen.findByLabelText('Timezone'), { target: { value: 'Asia/Colombo' } });
    openSection('People');
    expect(screen.getByRole('alertdialog')).toHaveTextContent('You have unsaved changes in Deployment.');
    expect(screen.getByRole('tab', { name: 'Deployment' })).toHaveAttribute('aria-selected', 'true');
  });

  it("shows a person's created date in the deployment zone", async () => {
    mount({
      users: [user({ created_at: '2026-09-17T20:00:00Z' })],
      deployment: { settings: { timezone: { value: 'Asia/Colombo', source: 'admin', env: 'TZ', fallback: 'UTC', fallback_source: 'default' } }, info: [] },
    });
    expect(await screen.findByText(/2026-09-18/)).toBeInTheDocument();
  });

  it('uses a zone saved in Deployment for the dates in People', async () => {
    const saved = { settings: { timezone: { value: 'Asia/Colombo', source: 'admin', env: 'TZ', fallback: 'UTC', fallback_source: 'default' } }, info: [] };
    mount({
      users: [user({ created_at: '2026-09-17T20:00:00Z' })],
      extra: (path, opts) => (path === '/v1/admin/settings/deployment' && opts?.method === 'PUT' ? json(200, saved) : undefined),
      section: null,
    });
    openSection('Deployment');
    fireEvent.change(await screen.findByLabelText('Timezone'), { target: { value: 'Asia/Colombo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByLabelText('Timezone')).toHaveValue('Asia/Colombo'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled());
    await act(async () => {});   // the section's "no unsaved changes" reaches the console in an effect, a tick after the button disables
    openSection('People');
    expect(await screen.findByText(/2026-09-18/)).toBeInTheDocument();
  });

  it('counts People dates in UTC when the zone cannot be loaded', async () => {
    mount({
      users: [user({ created_at: '2026-09-17T20:00:00Z' })],
      extra: (path) => (path === '/v1/admin/settings/deployment' ? json(500, {}) : undefined),
    });
    expect(await screen.findByText(/2026-09-17/)).toBeInTheDocument();
  });
});

describe('AdminConsole — assistant profile', () => {
  beforeEach(() => vi.clearAllMocks());

  it('has an Assistant section (v2.4 Task A2)', async () => {
    mount({ users: [user()] });
    openSection('Chat');
    expect(await screen.findByRole('region', { name: 'Assistant' })).toBeTruthy();
  });
});

describe('AdminConsole — unsaved drafts in Chat and Defaults', () => {
  beforeEach(() => vi.clearAllMocks());

  const assistant = { text: '', source: 'none', fileConfigured: false, maxChars: 8000, preview: '', warnings: [] };
  const BUILT_IN = { chatAutoTts: false, chatTtsMode: 'after-complete', think: 'off', selectedVoice: 'af_heart', playbackSpeed: 1 };
  const defaultsView = { values: { ...BUILT_IN }, builtIn: BUILT_IN, overridden: [], epochs: {},
    choices: { chatTtsMode: ['streaming', 'after-complete'], think: ['off', 'on', 'low', 'medium', 'high'] } };
  const routes = (path) => {
    if (path === '/v1/admin/assistant') return json(200, assistant);
    if (path === '/v1/admin/chat-options') return json(200, { web_search: 'ask', source: 'default', searxng: true });
    if (path === '/v1/admin/defaults') return json(200, defaultsView);
    return undefined;
  };

  it('asks before a tab switch drops an unsaved assistant profile', async () => {
    mount({ users: [user()], section: null, extra: routes });
    openSection('Chat');
    fireEvent.change(await screen.findByRole('textbox'), { target: { value: 'You are Sam.' } });
    openSection('Overview');
    expect(screen.getByRole('alertdialog')).toHaveTextContent('You have unsaved changes in Chat.');
    expect(screen.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  });

  it('does not ask when the assistant profile is untouched or back to what is saved', async () => {
    mount({ users: [user()], section: null, extra: routes });
    openSection('Chat');
    const box = await screen.findByRole('textbox');
    fireEvent.change(box, { target: { value: 'x' } });
    fireEvent.change(box, { target: { value: '' } });
    await act(async () => {});
    openSection('Overview');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  });

  it('asks before a tab switch drops an unsaved Defaults change', async () => {
    mount({ users: [user()], section: null, extra: routes });
    openSection('Defaults for users');
    fireEvent.click(await screen.findByRole('checkbox', { name: /read replies aloud/i }));
    openSection('Overview');
    expect(screen.getByRole('alertdialog')).toHaveTextContent('You have unsaved changes in Defaults for users.');
    expect(screen.getByRole('tab', { name: 'Defaults for users' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('AdminConsole — leaving through the app header', () => {
  beforeEach(() => vi.clearAllMocks());

  const deployment = { settings: { timezone: { value: 'UTC', source: 'default', env: 'TZ', fallback: 'UTC', fallback_source: 'default' } }, info: [] };
  // The app hands the console a ref; the header's view buttons and Log out call leaveRef.current(run).
  function mountWithLeaveRef() {
    const leaveRef = { current: null };
    apiFetch.mockImplementation(async (host, port, path) => {
      if (path === '/v1/admin/settings/deployment') return json(200, deployment);
      if (path === '/v1/admin/overview') return json(200, { checks: [] });
      return json(404, {});
    });
    render(<AdminConsole theme={theme} apiHost="" apiPort="" currentUserId="me" onBack={vi.fn()} leaveRef={leaveRef} showToast={vi.fn()} />);
    return leaveRef;
  }
  const dirtyDeployment = async () => {
    openSection('Deployment');
    fireEvent.change(await screen.findByLabelText('Timezone'), { target: { value: 'Asia/Colombo' } });
  };

  it('runs a header exit straight away when nothing is unsaved', async () => {
    const leaveRef = mountWithLeaveRef();
    const run = vi.fn();
    act(() => leaveRef.current(run));
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('asks first when a header exit (a view button or Log out) would drop an unsaved change', async () => {
    const leaveRef = mountWithLeaveRef();
    await dirtyDeployment();
    const run = vi.fn();
    act(() => leaveRef.current(run));
    expect(run).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toHaveTextContent('You have unsaved changes in Deployment.');
  });

  it('Keep editing stays; Discard changes then runs the exit', async () => {
    const leaveRef = mountWithLeaveRef();
    await dirtyDeployment();
    const run = vi.fn();
    act(() => leaveRef.current(run));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Keep editing' }));
    expect(run).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.getByLabelText('Timezone')).toHaveValue('Asia/Colombo');   // the draft is still there
    act(() => leaveRef.current(run));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Discard changes' }));
    expect(run).toHaveBeenCalledTimes(1);
  });
});

describe('AdminConsole — chat options', () => {
  beforeEach(() => vi.clearAllMocks());

  const assistantView = (preview) => ({ text: '', source: 'none', fileConfigured: false, maxChars: 8000, preview, warnings: [] });

  it('has a Chat options section after the Assistant section', async () => {
    mount({ users: [user()] });
    openSection('Chat');
    const assistant = await screen.findByRole('region', { name: 'Assistant' });
    const options = await screen.findByRole('region', { name: 'Chat options' });
    expect(assistant.compareDocumentPosition(options) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a saved web search mode refreshes what the model receives', async () => {
    let mode = { web_search: 'ask', source: 'default', searxng: true };
    mount({
      users: [user()],
      extra: (path, opts) => {
        if (path === '/v1/admin/chat-options' && opts?.method === 'PUT') {
          mode = { ...mode, web_search: JSON.parse(opts.body).web_search, source: 'admin' };
          return json(200, mode);
        }
        if (path === '/v1/admin/chat-options') return json(200, mode);
        if (path === '/v1/admin/assistant') {
          return json(200, assistantView(mode.web_search === 'auto' ? 'Rules: web_search, read_web_pages.' : 'Rules: offer_web_search.'));
        }
        return undefined;
      },
    });
    openSection('Chat');
    fireEvent.click(await screen.findByText('What the model receives'));
    expect(await screen.findByText('Rules: offer_web_search.')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('radio', { name: 'Search without asking' }));
    expect(await screen.findByText('Rules: web_search, read_web_pages.')).toBeInTheDocument();
    expect(screen.queryByText('Rules: offer_web_search.')).toBeNull();
  });
});
