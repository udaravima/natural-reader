import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react';
import { PhoneLinesSection } from './PhoneLinesSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const refuse = (status, reason, message) => json(status, { detail: { error: reason, reason, message } });

const SNIPPET = 'exten => 810,1,Answer()\n same => n,AudioSocket(line-1,192.0.2.10:9092)\n same => n,Hangup()';

const line = (over = {}) => ({
  id: 'line-1', name: 'Front desk', enabled: true,
  profile: { id: 'pr-1', name: 'The Librarian' }, project: { id: 'p-1', name: 'Product docs' },
  model: 'ollama:llama3', voice: { engine: 'kokoro', voice: 'af_heart', accent: null }, language: 'en',
  greeting: "Hello, you've reached Front desk. I'm an automated assistant. How can I help?",
  maxCallS: 600, maxCalls: 2, dailyTokens: null, callsToday: 3, lastCallAt: '2026-11-21T14:02:00Z',
  transcripts: 2, access: true, snippet: SNIPPET, ...over,
});

const call = (over = {}) => ({
  id: 'call-1', startedAt: '2026-11-21T14:02:00Z', endedAt: '2026-11-21T14:04:31Z', lengthS: 151, endReason: 'caller',
  turns: 4, slowestFirstSoundS: 2.9, transcript: true, ...over,
});

const VOICES = { engines: [
  { engine: 'kokoro', label: 'Kokoro', voices: ['af_heart', 'bm_george'] },
  { engine: 'chirp', label: 'Chirp 3 HD', voices: ['Aoede', 'Charon'], accents: [{ code: 'en-US', label: 'American' }, { code: 'en-GB', label: 'British' }] },
] };

// The server, as far as these tests need it: mutable lines, calls, and a log of what was sent.
function mount({ lines = [line()], calls = [call()], status = { listening: true, pbx_address: '192.0.2.10:9092', allow: ['192.0.2.1'], active: 0 },
  extra, props = {} } = {}) {
  const state = { lines: lines.map((l) => ({ ...l })), calls: calls.map((c) => ({ ...c })), sent: [] };
  apiFetch.mockImplementation(async (h, p, path, opts = {}) => {
    const method = opts.method ?? 'GET';
    const answered = extra?.(path, opts, state);
    if (answered) return answered;
    if (path === '/v1/admin/phone/status') return json(200, status);
    if (path === '/v1/admin/phone/voices') return json(200, VOICES);
    if (path === '/v1/admin/phone/lines' && method === 'GET') return json(200, state.lines);
    if (/^\/v1\/admin\/phone\/lines\/[^/]+$/.test(path) && method === 'GET') {
      state.reads = (state.reads ?? 0) + 1;
      const found = (state.fresh ?? state.lines).find((l) => l.id === path.split('/').pop());
      return found ? json(200, found) : refuse(404, 'not_found', "This phone line doesn't exist.");
    }
    if (path === '/v1/admin/phone/lines' && method === 'POST') {
      const body = JSON.parse(opts.body);
      state.sent.push({ method, path, body });
      const made = line({ id: 'line-new', enabled: body.enabled, name: body.name, model: body.model, voice: { accent: null, ...body.voice },
        greeting: body.greeting, profile: null, project: body.projectId ? { id: body.projectId, name: 'Product docs' } : null,
        callsToday: 0, lastCallAt: null, transcripts: 0, access: body.projectId ? true : null, snippet: SNIPPET.replace('line-1', 'line-new') });
      state.lines.push(made);
      return json(201, made);
    }
    if (/^\/v1\/admin\/phone\/lines\/[^/]+$/.test(path) && method === 'PATCH') {
      const body = JSON.parse(opts.body);
      state.sent.push({ method, path, body });
      const id = path.split('/').pop();
      const at = state.lines.findIndex((l) => l.id === id);
      state.lines[at] = { ...state.lines[at], ...(body.name && { name: body.name }), ...('enabled' in body && { enabled: body.enabled }),
        ...(body.greeting && { greeting: body.greeting }), ...(body.voice && { voice: { accent: null, ...body.voice } }) };
      return json(200, state.lines[at]);
    }
    if (/\/access$/.test(path) && method === 'POST') {
      state.sent.push({ method, path });
      const id = path.split('/')[5];
      const at = state.lines.findIndex((l) => l.id === id);
      state.lines[at] = { ...state.lines[at], access: true };
      return json(200, state.lines[at]);
    }
    if (/^\/v1\/admin\/phone\/lines\/[^/]+$/.test(path) && method === 'DELETE') {
      state.sent.push({ method, path });
      const id = path.split('/').pop();
      state.lines = state.lines.filter((l) => l.id !== id);
      return json(200, { transcripts: 2 });
    }
    if (/\/calls$/.test(path)) return json(200, state.calls);
    if (/\/clips\/greeting$/.test(path)) return { ok: true, status: 200, blob: async () => new Blob(['RIFF'], { type: 'audio/wav' }) };
    if (/^\/v1\/admin\/phone\/calls\/[^/]+$/.test(path) && method === 'GET') {
      return json(200, { ...call(), lineId: 'line-1', stats: [], messages: [
        { id: 'm1', role: 'assistant', content: 'Hello, you called the front desk.', timestamp: 't', status: 'complete', finishReason: 'stop' },
        { id: 'm2', role: 'user', content: 'What are your opening hours?', timestamp: 't', status: 'complete', finishReason: null },
      ] });
    }
    if (/^\/v1\/admin\/phone\/calls\/[^/]+$/.test(path) && method === 'DELETE') {
      state.sent.push({ method, path });
      state.lines = state.lines.map((l) => ({ ...l, transcripts: Math.max(0, l.transcripts - 1) }));
      state.calls = [];
      return json(200, { transcripts: 1 });
    }
    if (path === '/v1/admin/assistant/profiles') {
      return json(200, { profiles: [{ id: 'pr-1', name: 'The Librarian', enabled: true, isDefault: true }, { id: 'pr-2', name: 'Concierge', enabled: true, isDefault: false }] });
    }
    if (path === '/v1/projects') {
      return json(200, [{ id: 'p-1', name: 'Product docs', my_role: 'owner' }, { id: 'p-2', name: 'Handbook', my_role: 'maintainer' },
        { id: 'p-3', name: 'Not mine to give', my_role: 'reader' }]);
    }
    if (path === '/v1/inference/models') {
      return json(200, { models: [{ id: 'ollama:llama3', name: 'Llama 3', local: true }, { id: 'ollama:gemma', name: 'Gemma', local: true }] });
    }
    return json(404, {});
  });
  const all = { theme, apiHost: '', apiPort: '', showToast: vi.fn(), onDirtyChange: vi.fn(), ...props };
  render(<PhoneLinesSection {...all} />);
  return { state, props: all };
}

const choose = (label, value) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const editFirst = async () => fireEvent.click(await screen.findByRole('button', { name: 'Edit Front desk' }));

// What a test replaces on the shared globals, put back after it.
const realCreate = URL.createObjectURL;
const realRevoke = URL.revokeObjectURL;
const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

describe('PhoneLinesSection', () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
    if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
    else delete navigator.clipboard;
  });

  it('lists lines with profile, project and calls today', async () => {
    mount();
    const card = (await screen.findByText('Front desk')).closest('[data-line]');
    expect(within(card).getByText('On')).toBeTruthy();
    expect(within(card).getByText('The Librarian · Product docs')).toBeTruthy();
    expect(within(card).getByText(/3 calls today/)).toBeTruthy();
    expect(screen.getByText(/Listening for your phone system at 192\.0\.2\.10:9092 · 0 calls now/)).toBeTruthy();
  });

  it('says what to do when there are no lines yet', async () => {
    mount({ lines: [] });
    expect(await screen.findByText(/No phone lines yet/)).toBeTruthy();
  });

  it('shows the project warning once a project is picked', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'New line' }));
    await screen.findByLabelText('Knows');
    expect(screen.queryByText('Anyone who calls this line hears answers drawn from this project.')).toBeNull();
    choose('Knows', 'p-2');
    expect(screen.getByText('Anyone who calls this line hears answers drawn from this project.')).toBeTruthy();
  });

  it('offers only the projects the admin maintains, and the profiles and models the console has', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'New line' }));
    const knows = await screen.findByLabelText('Knows');
    await waitFor(() => expect(within(knows).getByRole('option', { name: 'Handbook' })).toBeTruthy());
    expect(within(knows).queryByRole('option', { name: 'Not mine to give' })).toBeNull();
    expect(within(screen.getByLabelText('Answers as')).getByRole('option', { name: 'Concierge' })).toBeTruthy();
    await waitFor(() => expect(within(screen.getByLabelText('Model')).getByRole('option', { name: 'Gemma' })).toBeTruthy());
  });

  it('shows the snippet with the line id and the PBX address', async () => {
    mount();
    await editFirst();
    const snippet = await screen.findByTestId('phone-snippet');
    expect(snippet.textContent).toContain('AudioSocket(line-1,192.0.2.10:9092)');
    expect(screen.getByText('Pick any free extension. The id is this line\'s.')).toBeTruthy();
  });

  it('copies the snippet', async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const { props } = mount();
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SNIPPET));
    expect(props.showToast).toHaveBeenCalledWith(expect.stringMatching(/copied/i), expect.anything());
  });

  it('says how to turn the listener on when it is off', async () => {
    mount({ lines: [line({ snippet: null })], status: { listening: false, pbx_address: null, allow: [], active: 0 } });
    await editFirst();
    expect(await screen.findByText('The phone listener is off. Set PHONE_AUDIOSOCKET_LISTEN and PHONE_AUDIOSOCKET_ALLOW, then restart.')).toBeTruthy();
    expect(screen.queryByTestId('phone-snippet')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Copy' })).toBeNull();
  });

  it('says how to set the PBX address when the listener is on but has none to give', async () => {
    mount({ lines: [line({ snippet: null })], status: { listening: true, pbx_address: null, allow: [], active: 0 } });
    await editFirst();
    expect(await screen.findByText('Set PHONE_PBX_ADDRESS to the address your phone system dials, then restart.')).toBeTruthy();
    expect(screen.queryByTestId('phone-snippet')).toBeNull();
  });

  it('plays the welcome from the clip endpoint', async () => {
    const created = []; const played = [];
    class FakeAudio {
      constructor(src) { this.src = src; created.push(this); }
      play() { played.push(this.src); return Promise.resolve(); }
    }
    vi.stubGlobal('Audio', FakeAudio);
    URL.createObjectURL = vi.fn(() => 'blob:welcome');
    URL.revokeObjectURL = vi.fn();
    mount();
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Hear the welcome' }));
    await waitFor(() => expect(played).toEqual(['blob:welcome']));
    expect(apiFetch.mock.calls.some(([, , p]) => p === '/v1/admin/phone/lines/line-1/clips/greeting')).toBe(true);
    created[0].onended();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:welcome');
  });

  it('disables the welcome while the welcome or voice has unsaved changes, and says why', async () => {
    mount();
    await editFirst();
    const hear = await screen.findByRole('button', { name: 'Hear the welcome' });
    expect(hear.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText('Welcome message'), { target: { value: 'Hi there.' } });
    expect(hear.disabled).toBe(true);
    expect(screen.getByText('Save to hear the new welcome.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Welcome message'), { target: { value: line().greeting } });
    expect(hear.disabled).toBe(false);
    choose('Voice', 'bm_george');
    expect(hear.disabled).toBe(true);
  });

  it('cannot play the welcome of a line that is not saved yet', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'New line' }));
    expect((await screen.findByRole('button', { name: 'Hear the welcome' })).disabled).toBe(true);
    expect(screen.getByText('Save to hear the new welcome.')).toBeTruthy();
  });

  it('prefills a new line\'s welcome with its name, until the admin edits the welcome', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'New line' }));
    const welcome = await screen.findByLabelText('Welcome message');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Spa' } });
    expect(welcome.value).toBe("Hello, you've reached Spa. I'm an automated assistant. How can I help?");
    fireEvent.change(welcome, { target: { value: 'Welcome to the spa.' } });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Spa line' } });
    expect(welcome.value).toBe('Welcome to the spa.');
  });

  it('creates a line (off), then lets the admin hear it and switch it on', async () => {
    const { state } = mount();
    URL.createObjectURL = vi.fn(() => 'blob:new');
    URL.revokeObjectURL = vi.fn();
    vi.stubGlobal('Audio', class { play() { return Promise.resolve(); } });
    fireEvent.click(await screen.findByRole('button', { name: 'New line' }));
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Spa' } });
    choose('Knows', 'p-2');
    await waitFor(() => expect(within(screen.getByLabelText('Model')).getByRole('option', { name: 'Gemma' })).toBeTruthy());
    choose('Model', 'ollama:gemma');
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toMatchObject({
      name: 'Spa', enabled: false, projectId: 'p-2', model: 'ollama:gemma', profileId: null,
      voice: { engine: 'kokoro', voice: 'af_heart' }, maxCallS: 600, maxCalls: 2, dailyTokens: null,
      greeting: "Hello, you've reached Spa. I'm an automated assistant. How can I help?",
    });
    // saved: now it can be heard, then switched on
    await waitFor(() => expect(screen.getByRole('button', { name: 'Hear the welcome' }).disabled).toBe(false));
    fireEvent.click(screen.getByLabelText('Line is on'));
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(2));
    expect(state.sent[1]).toMatchObject({ method: 'PATCH', path: '/v1/admin/phone/lines/line-new', body: { enabled: true } });
    expect(Object.keys(state.sent[1].body)).toEqual(['enabled']);
  });

  it('keeps Kokoro\'s voices to the ones /voices gave, and shows Chirp\'s accent', async () => {
    const { state } = mount();
    await editFirst();
    const voice = await screen.findByLabelText('Voice');
    expect(within(voice).getAllByRole('option').map((o) => o.value)).toEqual(['af_heart', 'bm_george']);
    choose('Voice engine', 'chirp');
    expect(within(screen.getByLabelText('Voice')).getAllByRole('option').map((o) => o.value)).toEqual(['Aoede', 'Charon']);
    choose('English accent', 'en-GB');
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toEqual({ voice: { engine: 'chirp', voice: 'Aoede', accent: 'en-GB' } });
  });

  it('sends only what changed on an edit', async () => {
    const { state } = mount();
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.change(screen.getByLabelText('Longest call (minutes)'), { target: { value: '15' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toEqual({ name: 'Reception', maxCallS: 900 });
  });

  it('reports unsaved changes to the console', async () => {
    const { props } = mount();
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    await waitFor(() => expect(props.onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Front desk' } });
    await waitFor(() => expect(props.onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it('shows the engine\'s own reason when a save fails with 502', async () => {
    mount({ extra: (path, opts) => (opts?.method === 'PATCH' ? refuse(502, 'voice_failed', 'Google said the voice quota is used up.') : undefined) });
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Google said the voice quota is used up.');
    expect(alert.textContent).not.toContain('Something went wrong on the server');
  });

  it.each([
    [409, 'line_changed', 'This line was changed by someone else meanwhile. Load it again and retry.'],
    [422, 'model_not_allowed', "That model isn't available on this server."],
    [422, 'no_project', 'A line needs a project before it can be switched on.'],
    [409, 'call_in_progress', 'A call is in progress on this line. Try again when it has ended.'],
  ])('shows the API\'s words for %s %s', async (status, reason, message) => {
    mount({ extra: (path, opts) => (opts?.method === 'PATCH' ? refuse(status, reason, message) : undefined) });
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    expect((await screen.findByRole('alert')).textContent).toContain(message);
  });

  it('shows the calls, newest first as the API gives them, and opens a transcript', async () => {
    mount();
    await editFirst();
    const row = (await screen.findByText('caller hung up')).closest('[data-call]');
    expect(within(row).getByText('2 m 31 s')).toBeTruthy();
    expect(within(row).getByText('4 turns')).toBeTruthy();
    expect(within(row).getByText('slowest 2.9 s')).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: 'Transcript' }));
    expect(await screen.findByText('What are your opening hours?')).toBeTruthy();
    expect(screen.getByText('Hello, you called the front desk.')).toBeTruthy();
    // the caller's words come after the greeting: order is the API's
    const text = screen.getByTestId('phone-transcript').textContent;
    expect(text.indexOf('Hello, you called')).toBeLessThan(text.indexOf('What are your'));
  });

  it('has no Transcript button for a call without one', async () => {
    mount({ calls: [call({ transcript: false, endReason: 'busy', turns: 0, slowestFirstSoundS: null })] });
    await editFirst();
    await screen.findByText('line was busy');
    expect(screen.queryByRole('button', { name: 'Transcript' })).toBeNull();
  });

  it('deletes a call after asking', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { state } = mount();
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete call' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/transcript/));
    await waitFor(() => expect(state.sent).toEqual([{ method: 'DELETE', path: '/v1/admin/phone/calls/call-1' }]));
    await waitFor(() => expect(screen.queryByText('caller hung up')).toBeNull());
  });

  it('asks before delete and names the transcripts lost', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { state } = mount();
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete line' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('Delete Front desk and its 2 call transcripts?'));
    expect(state.sent).toEqual([]);   // said no: nothing was sent
  });

  it('deletes the line when the admin says yes, and goes back to the list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { state, props } = mount();
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete line' }));
    await waitFor(() => expect(state.sent).toEqual([{ method: 'DELETE', path: '/v1/admin/phone/lines/line-1' }]));
    expect(await screen.findByText(/No phone lines yet/)).toBeTruthy();
    expect(props.showToast).toHaveBeenCalled();
  });

  it('shows a refused delete in the API\'s words', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    mount({ extra: (path, opts) => (opts?.method === 'DELETE' ? refuse(409, 'call_in_progress', 'A call is in progress on this line. Try again when it has ended.') : undefined) });
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete line' }));
    expect((await screen.findByRole('alert')).textContent).toContain('A call is in progress on this line.');
  });

  it('offers to give access again only when the line has lost it', async () => {
    const { state } = mount({ lines: [line({ access: false })] });
    await editFirst();
    expect(await screen.findByText('No access to Product docs')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Give it access again' }));
    await waitFor(() => expect(state.sent).toEqual([{ method: 'POST', path: '/v1/admin/phone/lines/line-1/access' }]));
    await waitFor(() => expect(screen.queryByText('No access to Product docs')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Give it access again' })).toBeNull();
  });

  it('shows no access button when access is fine, or when the line has no project', async () => {
    mount({ lines: [line({ project: null, access: null, enabled: false })] });
    await editFirst();
    await screen.findByLabelText('Knows');
    expect(screen.queryByRole('button', { name: 'Give it access again' })).toBeNull();
    expect(screen.queryByText(/No access to/)).toBeNull();
    cleanup();
    mount();   // access is fine
    await editFirst();
    await screen.findByLabelText('Knows');
    expect(screen.queryByRole('button', { name: 'Give it access again' })).toBeNull();
    expect(screen.queryByText(/No access to/)).toBeNull();
  });

  it('shows a refused restore in the API\'s words', async () => {
    mount({ lines: [line({ access: false })],
      extra: (path) => (path.endsWith('/access') ? refuse(403, 'insufficient_role', 'Only Maintainers or Owners can do that.') : undefined) });
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Give it access again' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Only Maintainers or Owners can do that.');
  });

  it('says so when the lines cannot be loaded, with a Retry', async () => {
    let fail = true;
    mount({ extra: (path, opts) => (path === '/v1/admin/phone/lines' && (opts?.method ?? 'GET') === 'GET' && fail ? json(500, {}) : undefined) });
    expect((await screen.findByRole('alert')).textContent).toMatch(/Could not load/);
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Front desk')).toBeTruthy();
  });

  // ---- fix round 1 (R12.8) ----

  it('shows saved values the server no longer offers, and a rename sends only the name', async () => {
    const gone = line({ project: { id: 'p-9', name: 'Old project' }, model: 'ollama:gone', profile: { id: 'pr-9', name: 'Retired' },
      voice: { engine: 'chirp', voice: 'Zephyr', accent: 'en-GB' } });
    const { state } = mount({ lines: [gone], extra: (path) => (path === '/v1/admin/phone/voices'
      ? json(200, { engines: [VOICES.engines[0]] }) : undefined) });
    await editFirst();
    await waitFor(() => expect(screen.getByLabelText('Model').value).toBe('ollama:gone'));
    expect(screen.getByLabelText('Knows').value).toBe('p-9');
    expect(screen.getByLabelText('Answers as').value).toBe('pr-9');
    expect(screen.getByLabelText('Voice engine').value).toBe('chirp');
    expect(screen.getByLabelText('Voice').value).toBe('Zephyr');
    expect(screen.getByLabelText('English accent').value).toBe('en-GB');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toEqual({ name: 'Reception' });
    // moving off the dropped engine, and back to the saved one, both work
    choose('Voice engine', 'kokoro');
    expect(screen.getByLabelText('Voice').value).toBe('af_heart');
    choose('Voice engine', 'chirp');
    expect(screen.getByLabelText('Voice engine').value).toBe('chirp');
  });

  it('reads the line again on Edit, so the delete prompt and a lost access are current', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { state } = mount();
    state.fresh = [line({ transcripts: 5, access: false })];   // changed since the list was read
    await editFirst();
    expect(await screen.findByText('No access to Product docs')).toBeTruthy();
    state.fresh = [line({ transcripts: 7, access: false })];   // and again before the question
    fireEvent.click(screen.getByRole('button', { name: 'Delete line' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('Delete Front desk and its 7 call transcripts?'));
  });

  it('offers "Load it again" after line_changed, and the form is made from the fresh line', async () => {
    const { state } = mount({ extra: (path, opts) => (opts?.method === 'PATCH'
      ? refuse(409, 'line_changed', 'This line was changed by someone else meanwhile. Load it again and retry.') : undefined) });
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    state.fresh = [line({ name: 'Theirs' })];
    fireEvent.click(await screen.findByRole('button', { name: 'Load it again' }));
    await waitFor(() => expect(screen.getByLabelText('Name').value).toBe('Theirs'));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('reloads the calls with Refresh, and drops the transcript count after deleting a call', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { state } = mount();
    await editFirst();
    await screen.findByText('caller hung up');
    state.calls = [call(), call({ id: 'call-2', endReason: 'idle' })];
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('no one spoke')).toBeTruthy();
    fireEvent.click(within(screen.getByText('no one spoke').closest('[data-call]')).getByRole('button', { name: 'Delete call' }));
    await waitFor(() => expect(state.sent.some((s) => s.path.endsWith('/calls/call-2'))).toBe(true));
    // the line's transcript count went from 2 to 1: the delete question says so
    window.confirm.mockClear();
    window.confirm.mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Delete line' }));
    await waitFor(() => expect(window.confirm).toHaveBeenCalledWith('Delete Front desk and its 1 call transcript?'));
  });

  it('reloads the list when going back', async () => {
    const { state } = mount();
    await editFirst();
    state.lines[0] = { ...state.lines[0], callsToday: 9 };
    fireEvent.click(await screen.findByRole('button', { name: 'Back to the lines' }));
    expect(await screen.findByText(/9 calls today/)).toBeTruthy();
  });

  it.each([
    ['Longest call (minutes)', '0', /Longest call must/],
    ['Longest call (minutes)', '', /Longest call must/],
    ['Calls at once', '21', /Calls at once must/],
    ['Calls at once', '1.5', /Calls at once must/],
    ['Tokens a day', '-5', /Tokens a day must/],
  ])('disables Save with a hint when %s is %j', async (label, value, hint) => {
    mount();
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(screen.getByRole('button', { name: 'Save line' }).disabled).toBe(true);
    expect(screen.getByText(hint)).toBeTruthy();
  });

  it('names the field of a Pydantic 422', async () => {
    mount({ extra: (path, opts) => (opts?.method === 'PATCH'
      ? json(422, { detail: [{ loc: ['body', 'maxCallS'], msg: 'Input should be greater than or equal to 60' }] }) : undefined) });
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Longest call');
    expect(alert.textContent).not.toContain('Request failed');
  });

  it('says who can restore access when the admin does not maintain the project, with no button', async () => {
    mount({ lines: [line({ project: { id: 'p-3', name: 'Not mine to give' }, access: false })] });
    await editFirst();
    expect(await screen.findByText('Only a maintainer or owner of Not mine to give can give it access again.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Give it access again' })).toBeNull();
  });

  it('tells the admin which projects are listed', async () => {
    mount();
    await editFirst();
    expect(await screen.findByText('Only projects you maintain or own are listed.')).toBeTruthy();
  });

  it('reads Closed, not On, for a line the gate turns away, and lets it be switched off', async () => {
    mount({ lines: [line({ project: null, access: null })] });
    const card = (await screen.findByText('Front desk')).closest('[data-line]');
    expect(within(card).getByText('Closed')).toBeTruthy();
    expect(within(card).queryByText('On')).toBeNull();
    await editFirst();
    expect((await screen.findByLabelText('Line is on')).disabled).toBe(false);
  });

  it('uses the PBX-address sentence in the header when the listener is on without an address', async () => {
    mount({ status: { listening: true, pbx_address: null, allow: [], active: 0 } });
    expect(await screen.findByText('Set PHONE_PBX_ADDRESS to the address your phone system dials, then restart.')).toBeTruthy();
  });

  it('plays nothing and leaks nothing when the editor closed before the clip arrived', async () => {
    const played = vi.fn();
    vi.stubGlobal('Audio', class { play() { played(); return Promise.resolve(); } });
    URL.createObjectURL = vi.fn(() => 'blob:late');
    URL.revokeObjectURL = vi.fn();
    let release;
    const gate = new Promise((r) => { release = r; });
    mount({ extra: (path) => (/\/clips\/greeting$/.test(path)
      ? gate.then(() => ({ ok: true, status: 200, blob: async () => new Blob(['x']) })) : undefined) });
    await editFirst();
    fireEvent.click(await screen.findByRole('button', { name: 'Hear the welcome' }));
    fireEvent.click(screen.getByRole('button', { name: 'Back to the lines' }));
    await screen.findByRole('button', { name: 'New line' });
    release();
    await new Promise((r) => setTimeout(r, 20));
    expect(played).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('shows "in progress" once for a running call, and asks plainly about a call with no transcript', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    mount({ calls: [call({ endedAt: null, lengthS: null, endReason: null, transcript: false })] });
    await editFirst();
    const row = (await screen.findByText('in progress')).closest('[data-call]');
    expect(within(row).getAllByText('in progress')).toHaveLength(1);
    fireEvent.click(within(row).getByRole('button', { name: 'Delete call' }));
    expect(confirm).toHaveBeenCalledWith('Delete this call?');
  });

  // ---- fix round 2 (R12.9) ----

  it('restores the saved voice and accent when the saved engine is picked back, and the form is clean', async () => {
    const gone = line({ voice: { engine: 'chirp', voice: 'Zephyr', accent: 'en-GB' } });
    const { state, props } = mount({ lines: [gone], extra: (path) => (path === '/v1/admin/phone/voices'
      ? json(200, { engines: [VOICES.engines[0]] }) : undefined) });
    await editFirst();
    await screen.findByLabelText('Voice engine');
    choose('Voice engine', 'kokoro');
    choose('Voice engine', 'chirp');
    expect(screen.getByLabelText('Voice').value).toBe('Zephyr');
    expect(screen.getByLabelText('English accent').value).toBe('en-GB');
    await waitFor(() => expect(props.onDirtyChange).toHaveBeenLastCalledWith(false));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toEqual({ name: 'Reception' });
  });

  it('names an engine the server dropped by its display name', async () => {
    mount({ lines: [line({ voice: { engine: 'chirp', voice: 'Zephyr', accent: null } })],
      extra: (path) => {
        if (path === '/v1/admin/phone/voices') return json(200, { engines: [VOICES.engines[0]] });
        if (path === '/v1/admin/chat-voice') return json(200, { engines: [{ id: 'chirp', label: 'Chirp 3 HD', available: false }] });
        return undefined;
      } });
    await editFirst();
    await waitFor(() => expect(within(screen.getByLabelText('Voice engine')).getByRole('option', { name: 'Chirp 3 HD (not set up here)' })).toBeTruthy());
  });

  it('shows Opening and holds Edit and New line while a line is read; the editor then opens on that line', async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    mount({ extra: (path, opts) => (/lines\/line-1$/.test(path) && (opts?.method ?? 'GET') === 'GET'
      ? gate.then(() => json(200, line({ name: 'Front desk' }))) : undefined) });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Front desk' }));
    expect(await screen.findByText('Opening…')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'New line' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Edit Front desk' }).disabled).toBe(true);
    release();
    expect((await screen.findByLabelText('Name')).value).toBe('Front desk');
    expect(screen.queryByText('Opening…')).toBeNull();
  });

  it('shows a failed Edit read in the list, with the API\'s words', async () => {
    mount({ extra: (path, opts) => (/lines\/line-1$/.test(path) && (opts?.method ?? 'GET') === 'GET'
      ? refuse(404, 'not_found', "This phone line doesn't exist.") : undefined) });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Front desk' }));
    expect((await screen.findByRole('alert')).textContent).toContain("This phone line doesn't exist.");
    expect(screen.queryByLabelText('Name')).toBeNull();
  });

  it('does not claim only a maintainer can restore access until the projects have loaded', async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    mount({ lines: [line({ project: { id: 'p-3', name: 'Not mine to give' }, access: false })],
      extra: (path) => (path === '/v1/projects' ? gate.then(() => json(200, [{ id: 'p-1', name: 'Product docs', my_role: 'owner' }])) : undefined) });
    await editFirst();
    await screen.findByText('No access to Not mine to give');
    expect(screen.queryByText(/Only a maintainer or owner of/)).toBeNull();
    release();
    expect(await screen.findByText('Only a maintainer or owner of Not mine to give can give it access again.')).toBeTruthy();
  });

  it('never quotes seconds in a longest-call 422', async () => {
    mount({ extra: (path, opts) => (opts?.method === 'PATCH'
      ? json(422, { detail: [{ loc: ['body', 'maxCallS'], msg: 'Input should be less than or equal to 3600' }] }) : undefined) });
    await editFirst();
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    const text = (await screen.findByRole('alert')).textContent;
    expect(text).toContain('Longest call');
    expect(text).toContain('minutes');
    expect(text).not.toContain('3600');
  });

  it('shows 90 seconds as 1.5 minutes and saves it untouched', async () => {
    const { state } = mount({ lines: [line({ maxCallS: 90 })] });
    await editFirst();
    expect((await screen.findByLabelText('Longest call (minutes)')).value).toBe('1.5');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toEqual({ name: 'Reception' });
    fireEvent.change(screen.getByLabelText('Longest call (minutes)'), { target: { value: '2.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(2));
    expect(state.sent[1].body).toEqual({ maxCallS: 150 });
  });

  it('reads Closed when the line is on but has lost access to its project', async () => {
    mount({ lines: [line({ access: false })] });
    const card = (await screen.findByText('Front desk')).closest('[data-line]');
    expect(within(card).getByText('Closed')).toBeTruthy();
  });

  it('unticking Line is on and saving sends only enabled false', async () => {
    const { state } = mount();
    await editFirst();
    fireEvent.click(await screen.findByLabelText('Line is on'));
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(state.sent).toHaveLength(1));
    expect(state.sent[0].body).toEqual({ enabled: false });
  });

  it('reads the calls again after a save', async () => {
    const { state } = mount();
    await editFirst();
    await screen.findByText('caller hung up');
    state.calls = [call(), call({ id: 'call-2', endReason: 'idle' })];
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reception' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    expect(await screen.findByText('no one spoke')).toBeTruthy();
  });
});
