import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { AssistantProfileSection } from './AssistantProfileSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const DRAFT = 'You are Ada, a calm tutor.';
const BASE = '/v1/admin/assistant';

/** The library routes, kept in memory (server/routers/admin.py). `refuse` answers one path+method with an error. */
const MODELS = [{ id: 'ollama:big', name: 'big', local: true }, { id: 'cloud:gpt', name: 'gpt', local: false }];

function fakeServer({ profiles = [], house = '', file = { configured: false, inUse: false, text: '' }, helper = true,
  refuse = {}, draft = () => json(200, { name: '', description: '', backstory: '', text: DRAFT, warnings: [] }),
  describe = () => json(200, { description: 'A calm tutor who explains step by step.' }),
  sample = () => json(200, { reply: 'Hello, I am Ada.' }), models = MODELS } = {}) {
  const state = { house, profiles: profiles.map((p, i) => ({ id: `p${i + 1}`, description: '', backstory: '',
    enabled: true, isDefault: i === 0, chats: 0, warnings: [], ...p })) };
  const combined = (t) => [state.house, t].filter(Boolean).join('\n\n').length;
  const view = () => ({
    houseRules: { text: state.house, maxChars: 2000, warnings: [] },
    profiles: state.profiles.map((p) => ({ ...p, combinedChars: combined(p.text) })),
    file, maxChars: 8000, backstoryMaxChars: 6000, nameMaxChars: 60, descriptionMaxChars: 200,
    placeholders: ['date', 'app', 'profile'],
  });
  apiFetch.mockImplementation(async (host, port, path, opts = {}) => {
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : null;
    const key = `${method} ${path}`;
    if (refuse[key]) return refuse[key];
    if (path === '/v1/settings/helper') return json(200, { on: helper });
    if (path === '/v1/inference/models') return json(200, { models });
    if (key === `POST ${BASE}/describe`) return describe(body);
    if (key === `POST ${BASE}/sample`) return sample(body);
    if (key === `GET ${BASE}/profiles`) return json(200, view());
    if (key === `POST ${BASE}/profiles`) {
      state.profiles.push({ id: `p${state.profiles.length + 1}`, enabled: true, isDefault: !state.profiles.length,
        chats: 0, warnings: [], description: '', ...body });
      return json(200, view());
    }
    if (key === `PUT ${BASE}/profiles/order`) {
      state.profiles.sort((a, b) => body.ids.indexOf(a.id) - body.ids.indexOf(b.id));
      return json(200, view());
    }
    const id = path.match(/\/profiles\/([^/]+)$/)?.[1];
    if (id && method === 'PUT') {
      if (body.isDefault) state.profiles.forEach((p) => { p.isDefault = false; });
      Object.assign(state.profiles.find((p) => p.id === id), body);
      return json(200, view());
    }
    if (id && method === 'DELETE') {
      const chats = state.profiles.find((p) => p.id === id).chats;
      state.profiles = state.profiles.filter((p) => p.id !== id);
      return json(200, { ...view(), chats });
    }
    if (key === `PUT ${BASE}/house-rules`) { state.house = body.text; return json(200, view()); }
    if (key === `POST ${BASE}/preview`) {
      return json(200, { preview: `PREVIEW ${body.name}: ${body.text}`, combinedChars: combined(body.text),
        warnings: body.text.includes('{{x}}') ? ['{{x}} is not a placeholder.'] : [] });
    }
    if (key === `POST ${BASE}/draft`) return draft(body);
    return json(404, {});
  });
  return state;
}

function mount(props = {}) {
  const showToast = vi.fn();
  const onDirtyChange = vi.fn();
  const utils = render(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={showToast}
    onDirtyChange={onDirtyChange} {...props} />);
  return { showToast, onDirtyChange, ...utils };
}

const calls = (method, path) => apiFetch.mock.calls.filter(([, , p, o]) => p === path && (o?.method || 'GET') === method);
const sent = (method, path, i = 0) => JSON.parse(calls(method, path)[i][3].body);
const row = (name) => screen.getByRole('listitem', { name });

const TWO = [{ name: 'Plain', text: 'You are plain.', description: 'Short answers', chats: 3 },
  { name: 'Tutor', text: 'You are a tutor.', enabled: false }];

describe('AssistantProfileSection: the library', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('lists the profiles with their marks, descriptions and how many chats use them', async () => {
    fakeServer({ profiles: TWO });
    mount();
    const plain = await screen.findByRole('listitem', { name: 'Plain' });
    expect(within(plain).getByText('Default')).toBeTruthy();
    expect(within(plain).getByText('Short answers')).toBeTruthy();
    expect(within(plain).getByText('Used in 3 chats')).toBeTruthy();
    expect(within(row('Tutor')).getByText('Off')).toBeTruthy();
    // The default can't be turned off or deleted; another can't be made default while off.
    expect(within(plain).queryByRole('button', { name: 'Turn off Plain' })).toBeNull();
    expect(within(plain).queryByRole('button', { name: 'Delete Plain' })).toBeNull();
    expect(within(row('Tutor')).queryByRole('button', { name: 'Make Tutor the default' })).toBeNull();
  });

  it('shows the saved warnings under a profile', async () => {
    fakeServer({ profiles: [{ name: 'Plain', text: 'Use web_search.', warnings: ['It names web_search.'] }] });
    mount();
    expect(await screen.findByText('It names web_search.')).toBeTruthy();
  });

  it('saves the house rules, and only when they changed', async () => {
    fakeServer({ profiles: TWO });
    const { showToast, onDirtyChange } = mount();
    const box = await screen.findByLabelText('House rules');
    const save = screen.getByRole('button', { name: 'Save house rules' });
    expect(save.disabled).toBe(true);
    fireEvent.change(box, { target: { value: 'Be kind.' } });
    expect(screen.getByText(/8 \/ 2,000 characters/)).toBeTruthy();
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(save);
    await waitFor(() => expect(calls('PUT', `${BASE}/house-rules`)).toHaveLength(1));
    expect(sent('PUT', `${BASE}/house-rules`)).toEqual({ text: 'Be kind.' });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('House rules saved: they apply from the next message.', 4000));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it('says the shared rules belong in House rules while they are empty and there are two profiles', async () => {
    fakeServer({ profiles: TWO });
    mount();
    expect(await screen.findByText(/each profile is sent on its own/)).toBeTruthy();
  });

  it('adds a profile', async () => {
    fakeServer({ profiles: TWO });
    const { showToast } = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
    fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Reviewer' } });
    fireEvent.change(screen.getByLabelText('Profile description'), { target: { value: 'Reads twice' } });
    fireEvent.change(screen.getByLabelText('Rules'), { target: { value: 'You are careful.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(calls('POST', `${BASE}/profiles`)).toHaveLength(1));
    expect(sent('POST', `${BASE}/profiles`)).toEqual({ name: 'Reviewer', description: 'Reads twice', backstory: '', text: 'You are careful.' });
    expect(await screen.findByRole('listitem', { name: 'Reviewer' })).toBeTruthy();
    expect(screen.queryByLabelText('Rules')).toBeNull();                       // the editor closed
    expect(showToast).toHaveBeenCalledWith('Profile saved: it applies from the next message.', 4000);
  });

  it('edits a profile and sends only its words', async () => {
    fakeServer({ profiles: TWO });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    const text = screen.getByLabelText('Rules');
    expect(text.value).toBe('You are plain.');
    expect(screen.getByLabelText('Profile name').value).toBe('Plain');
    expect(screen.getByRole('button', { name: 'Save profile' }).disabled).toBe(true);
    fireEvent.change(text, { target: { value: 'You are plain and brief.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(calls('PUT', `${BASE}/profiles/p1`)).toHaveLength(1));
    expect(sent('PUT', `${BASE}/profiles/p1`)).toEqual({ name: 'Plain', description: 'Short answers', backstory: '', text: 'You are plain and brief.' });
  });

  it('counts the profile with the house rules, and lists the placeholders', async () => {
    fakeServer({ profiles: TWO, house: 'Be kind.' });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    // 14 characters, 8 + 2 + 14 = 24 with the house rules.
    expect(screen.getByText(/14 \/ 8,000 characters · 24 with house rules/)).toBeTruthy();
    expect(screen.getByText('{{date}}')).toBeTruthy();
    expect(screen.getByText('{{app}}')).toBeTruthy();
    expect(screen.getByText('{{profile}}')).toBeTruthy();
  });

  it('asks before Cancel drops unsaved changes', async () => {
    fakeServer({ profiles: TWO });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { onDirtyChange } = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    fireEvent.change(screen.getByLabelText('Rules'), { target: { value: 'Changed.' } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(confirm).toHaveBeenCalledWith('Discard the unsaved changes to this profile?');
    expect(screen.getByLabelText('Rules').value).toBe('Changed.');
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('Rules')).toBeNull();
  });

  it('makes another the default, turns one on, and moves them', async () => {
    fakeServer({ profiles: [...TWO, { name: 'Reviewer', text: 'You are careful.' }] });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on Tutor' }));
    await waitFor(() => expect(sent('PUT', `${BASE}/profiles/p2`)).toEqual({ enabled: true }));
    fireEvent.click(await screen.findByRole('button', { name: 'Make Tutor the default' }));
    await waitFor(() => expect(sent('PUT', `${BASE}/profiles/p2`, 1)).toEqual({ isDefault: true }));
    await waitFor(() => expect(within(row('Tutor')).getByText('Default')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Move Reviewer up' }));
    await waitFor(() => expect(sent('PUT', `${BASE}/profiles/order`)).toEqual({ ids: ['p1', 'p3', 'p2'] }));
    expect(screen.getAllByRole('listitem').map((li) => li.getAttribute('aria-label'))).toEqual(['Plain', 'Reviewer', 'Tutor']);
    expect(screen.getByRole('button', { name: 'Move Plain up' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Move Tutor down' }).disabled).toBe(true);
  });

  it('deletes after saying how many chats move to the default', async () => {
    fakeServer({ profiles: [{ name: 'Plain', text: 'x' }, { name: 'Tutor', text: 'y', chats: 2 }] });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { showToast } = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Tutor' }));
    expect(confirm).toHaveBeenCalledWith('Delete the profile "Tutor"? 2 chats use it; they will use the default from their next message.');
    expect(calls('DELETE', `${BASE}/profiles/p2`)).toHaveLength(0);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Delete Tutor' }));
    await waitFor(() => expect(screen.queryByRole('listitem', { name: 'Tutor' })).toBeNull());
    expect(showToast).toHaveBeenCalledWith('Profile deleted. 2 chats will use the default from their next message.', 5000);
  });

  it('words the question for one chat', async () => {
    fakeServer({ profiles: [{ name: 'Plain', text: 'x' }, { name: 'Tutor', text: 'y', chats: 1 }] });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Tutor' }));
    expect(confirm).toHaveBeenCalledWith('Delete the profile "Tutor"? 1 chat uses it; it will use the default from its next message.');
  });

  it("shows the server's reason when it refuses, and keeps the editor open", async () => {
    fakeServer({ profiles: TWO, refuse: { [`POST ${BASE}/profiles`]: json(409, { detail: { error: 'name_taken', message: 'Another profile has that name.' } }) } });
    const { showToast } = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
    fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'plain' } });
    fireEvent.change(screen.getByLabelText('Rules'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('Another profile has that name.', 5000));
    expect(screen.getByLabelText('Rules').value).toBe('x');
  });

  it('starts the first profile from the deployment file while that file is the profile', async () => {
    fakeServer({ file: { configured: true, inUse: true, text: 'From the file.' } });
    mount();
    expect(await screen.findByText(/The deployment file is the profile until you add one/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add profile' }));
    expect(screen.getByLabelText('Rules').value).toBe('From the file.');
  });

  it('says there is no profile when the library and the file are empty', async () => {
    fakeServer();
    mount();
    expect(await screen.findByText(/No profiles yet/)).toBeTruthy();
  });

  it('shows what the model receives for the text being edited, and fetches it again on refreshKey', async () => {
    fakeServer({ profiles: TWO });
    const { rerender, showToast, onDirtyChange } = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    fireEvent.change(screen.getByLabelText('Rules'), { target: { value: 'Today is {{x}}.' } });
    fireEvent.click(screen.getByText('What the model receives'));
    expect(await screen.findByText('PREVIEW Plain: Today is {{x}}.')).toBeTruthy();
    expect(screen.getByText('{{x}} is not a placeholder.')).toBeTruthy();
    expect(sent('POST', `${BASE}/preview`)).toEqual({ name: 'Plain', backstory: '', text: 'Today is {{x}}.' });
    rerender(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={showToast} onDirtyChange={onDirtyChange} refreshKey={1} />);
    await waitFor(() => expect(calls('POST', `${BASE}/preview`)).toHaveLength(2));
    expect(screen.getByLabelText('Rules').value).toBe('Today is {{x}}.');      // what is being typed stays
  });

  it('says so when the library cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    mount();
    expect(await screen.findByText(/Couldn't load the assistant profiles/)).toBeTruthy();
  });

  // Helper spec §7: the helper model drafts a profile from a description; nothing is saved until Save.
  describe('Generate', () => {
    const openEditor = async () => fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    const describe_ = (text) => {
      fireEvent.change(screen.getByLabelText('Describe the character'), { target: { value: text } });
      fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    };
    const drafts = () => calls('POST', `${BASE}/draft`);

    it('without a helper, a chat model writes it; with neither, it says where to set one up', async () => {
      fakeServer({ profiles: TWO, helper: false });
      const first = mount();
      await openEditor();
      const by = await screen.findByLabelText('Written by');
      await waitFor(() => expect([...by.options].map((o) => o.textContent)).toEqual(
        ['big', 'gpt (cloud: your notes leave this machine)']));
      describe_('A calm tutor');
      await waitFor(() => expect(drafts()).toHaveLength(1));
      expect(sent('POST', `${BASE}/draft`)).toEqual({ description: 'A calm tutor', model: 'ollama:big' });
      first.unmount();
      vi.clearAllMocks();
      fakeServer({ profiles: TWO, helper: false, models: [] });
      mount();
      await openEditor();
      expect(await screen.findByText('Choose a helper model in Models, or add a chat model, to generate this.'))
        .toBeTruthy();
    });

    it('puts the draft in the editor and saves nothing', async () => {
      fakeServer({ profiles: TWO });
      const { showToast } = mount();
      fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
      expect((await screen.findByRole('button', { name: 'Generate' })).disabled).toBe(true);   // nothing described
      describe_('A calm tutor called Ada');
      await waitFor(() => expect(screen.getByLabelText('Rules').value).toBe(DRAFT));
      expect(sent('POST', `${BASE}/draft`)).toEqual({ description: 'A calm tutor called Ada' });
      expect(calls('POST', `${BASE}/profiles`)).toHaveLength(0);
      expect(showToast).toHaveBeenCalledWith('Draft placed in the editor. Nothing is saved until you press Save profile.', 5000);
    });

    it('shows that it is writing, and takes no second request meanwhile', async () => {
      let finish;
      fakeServer({ profiles: TWO, draft: () => new Promise((resolve) => { finish = () => resolve(json(200, { text: DRAFT, warnings: [] })); }) });
      mount();
      await openEditor();
      await screen.findByRole('button', { name: 'Generate' });
      describe_('A calm tutor');
      const busy = await screen.findByRole('button', { name: 'Writing a draft…' });
      expect(busy.disabled).toBe(true);
      fireEvent.click(busy);
      expect(drafts()).toHaveLength(1);
      finish();
      expect(await screen.findByRole('button', { name: 'Generate' })).toBeTruthy();
    });

    it('asks before replacing unsaved text, and keeps it when told no', async () => {
      fakeServer({ profiles: TWO });
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
      mount();
      await openEditor();
      await screen.findByRole('button', { name: 'Generate' });
      fireEvent.change(screen.getByLabelText('Rules'), { target: { value: 'Be brief. And kind.' } });
      describe_('A calm tutor');
      await waitFor(() => expect(confirm).toHaveBeenCalledWith(
        'Replace the unsaved backstory and rules in the editor with the draft?'));
      expect(screen.getByLabelText('Rules').value).toBe('Be brief. And kind.');
      confirm.mockReturnValue(true);
      fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
      await waitFor(() => expect(screen.getByLabelText('Rules').value).toBe(DRAFT));
    });

    it("shows the draft's warnings and the server's reason when it can't", async () => {
      fakeServer({ profiles: TWO, draft: () => json(200, { text: 'Use web_search.', warnings: ['It names web_search.'] }) });
      const { showToast } = mount();
      fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
      await screen.findByRole('button', { name: 'Generate' });
      describe_('A searcher');
      expect(await screen.findByText('It names web_search.')).toBeTruthy();
      fakeServer({ profiles: TWO, draft: () => json(429, { detail: { error: 'budget_exhausted', message: 'Daily inference budget exhausted.' } }) });
      fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
      await waitFor(() => expect(showToast).toHaveBeenCalledWith('Daily inference budget exhausted.', 5000));
      expect(screen.getByLabelText('Rules').value).toBe('Use web_search.');
    });
  });
});

// The Librarian, the built-in profile (character profiles spec).
const WITH_LIBRARIAN = [{ name: 'The Story Teller', text: 'You are an old storyteller.' },
  { name: 'The Librarian', text: '', builtin: true, description: 'Careful answers from your documents, with page numbers.' }];

describe('AssistantProfileSection: The Librarian', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('marks it built in and offers no Delete or Turn off, but can make it the default and move it', async () => {
    fakeServer({ profiles: WITH_LIBRARIAN });
    mount();
    const lib = await screen.findByRole('listitem', { name: 'The Librarian' });
    expect(within(lib).getByText('Built in')).toBeTruthy();
    expect(within(lib).getByText("The app's own document assistant, always here: it can be renamed, not deleted or "
      + 'turned off.')).toBeTruthy();
    expect(within(lib).queryByRole('button', { name: 'Delete The Librarian' })).toBeNull();
    expect(within(lib).queryByRole('button', { name: 'Turn off The Librarian' })).toBeNull();
    expect(within(lib).getByRole('button', { name: 'Move The Librarian up' })).toBeTruthy();
    fireEvent.click(within(lib).getByRole('button', { name: 'Make The Librarian the default' }));
    await waitFor(() => expect(calls('PUT', `${BASE}/profiles/p2`)).toHaveLength(1));
    expect(sent('PUT', `${BASE}/profiles/p2`)).toEqual({ isDefault: true });
  });

  it('edits only its name and description', async () => {
    fakeServer({ profiles: WITH_LIBRARIAN });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit The Librarian' }));
    expect(screen.queryByLabelText('Rules')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Generate' })).toBeNull();
    expect(screen.queryByRole('button', { name: /What the model receives/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Ada' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(calls('PUT', `${BASE}/profiles/p2`)).toHaveLength(1));
    expect(sent('PUT', `${BASE}/profiles/p2`)).toEqual({
      name: 'Ada', description: 'Careful answers from your documents, with page numbers.' });
  });

  it('counts only profiles an admin wrote as "no profiles yet"', async () => {
    fakeServer({ profiles: [WITH_LIBRARIAN[1]], file: { configured: true, inUse: true, text: 'From the file.' } });
    mount();
    expect(await screen.findByText(/The deployment file is the profile until you add one/)).toBeTruthy();
  });
});

// Characters with a backstory (character profiles spec, step 2).
describe('AssistantProfileSection: a character', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());
  const FOUR = { name: 'Grandpa Siri', description: 'A warm old storyteller.', backstory: 'You are Siri, eighty years old.',
    text: '- Stay in character.', warnings: [] };

  it('keeps a backstory above the rules, counts both, and saves it', async () => {
    fakeServer({ profiles: TWO, house: 'Be kind.' });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    fireEvent.change(screen.getByLabelText('Backstory'), { target: { value: 'You grew up by the sea.' } });
    expect(screen.getByText(/with house rules and backstory/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(calls('PUT', `${BASE}/profiles/p1`)).toHaveLength(1));
    expect(sent('PUT', `${BASE}/profiles/p1`)).toEqual({ name: 'Plain', description: 'Short answers',
      backstory: 'You grew up by the sea.', text: 'You are plain.' });
  });

  it('Generate fills the backstory and rules, and the name and description only where empty', async () => {
    fakeServer({ profiles: TWO, draft: () => json(200, FOUR) });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Add profile' }));
    fireEvent.change(await screen.findByLabelText('Describe the character'), { target: { value: 'An old storyteller' } });
    fireEvent.change(screen.getByLabelText('Written by'), { target: { value: 'cloud:gpt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await waitFor(() => expect(screen.getByLabelText('Backstory').value).toBe('You are Siri, eighty years old.'));
    expect(sent('POST', `${BASE}/draft`)).toEqual({ description: 'An old storyteller', model: 'cloud:gpt' });
    expect(screen.getByLabelText('Rules').value).toBe('- Stay in character.');
    expect(screen.getByLabelText('Profile name').value).toBe('Grandpa Siri');
    expect(screen.getByLabelText('Profile description').value).toBe('A warm old storyteller.');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));        // a saved name and description
    fireEvent.change(await screen.findByLabelText('Describe the character'), { target: { value: 'Older' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await waitFor(() => expect(screen.getByLabelText('Backstory').value).toBe('You are Siri, eighty years old.'));
    expect(screen.getByLabelText('Profile name').value).toBe('Plain');
    expect(screen.getByLabelText('Profile description').value).toBe('Short answers');
  });

  it('writes the description from the profile', async () => {
    fakeServer({ profiles: TWO });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Write the description' }));
    await waitFor(() => expect(screen.getByLabelText('Profile description').value)
      .toBe('A calm tutor who explains step by step.'));
    expect(sent('POST', `${BASE}/describe`)).toEqual({ name: 'Plain', backstory: '', text: 'You are plain.' });
  });

  it('Try it asks the unsaved profile one question and shows the answer', async () => {
    fakeServer({ profiles: TWO });
    const { showToast } = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    expect(screen.getByLabelText('Question').value).toBe('Introduce yourself.');
    fireEvent.change(screen.getByLabelText('Rules'), { target: { value: 'You are Ada.' } });
    await waitFor(() => expect(screen.getByLabelText('Answered by').value).toBe('ollama:big'));
    fireEvent.click(screen.getByRole('button', { name: 'Try it' }));
    expect(await screen.findByText('Hello, I am Ada.')).toBeTruthy();
    expect(sent('POST', `${BASE}/sample`)).toEqual({ name: 'Plain', backstory: '', text: 'You are Ada.',
      question: 'Introduce yourself.', model: 'ollama:big' });
    expect(calls('PUT', `${BASE}/profiles/p1`)).toHaveLength(0);                       // nothing saved
    fakeServer({ profiles: TWO, sample: () => json(422, { detail: { error: 'unknown_model',
      message: "That model isn't available any more. Choose another." } }) });
    fireEvent.click(screen.getByRole('button', { name: 'Try it' }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith("That model isn't available any more. Choose another.", 5000));
    expect(screen.getByLabelText('Rules').value).toBe('You are Ada.');
  });

  it("The Librarian's editor has no backstory, Try it or written description", async () => {
    fakeServer({ profiles: WITH_LIBRARIAN });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit The Librarian' }));
    expect(screen.queryByLabelText('Backstory')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Try it' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Write the description' })).toBeNull();
  });

  // From the final review: with no helper, the first model wrote the draft even when it was a cloud one, so notes could
  // leave the machine without anyone choosing that. Both pickers start on a local model when there is one.
  it('starts both pickers on a local model, so nothing leaves the machine unless chosen', async () => {
    fakeServer({ profiles: TWO, helper: false, models: [MODELS[1], MODELS[0]] });   // the cloud one listed first
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Plain' }));
    await waitFor(() => expect(screen.getByLabelText('Written by').value).toBe('ollama:big'));
    expect(screen.getByLabelText('Answered by').value).toBe('ollama:big');
  });
});
