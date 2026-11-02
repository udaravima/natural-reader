import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SpeechModelPicker } from './SpeechModelPicker';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', border: '', text: '', textSecondary: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const LIST = '/v1/admin/speech-models';

const small = {
  id: 'small', label: 'Whisper small', engine: 'faster-whisper', languages: [], language_names: [],
  license: '', commercial: '', note: '', removable: false, why_not: '',
};
const tamil = {
  ...small, id: 'whisper-small-ta', label: 'Whisper small, Tamil', engine: 'converted-whisper', languages: ['ta'],
  language_names: ['Tamil'], license: 'apache-2.0', commercial: 'yes', note: 'Test it first.',
};
const mine = { ...small, id: 'owner-name', label: 'Mine', engine: 'converted-whisper', removable: true };
const view = (over = {}) => ({ engine: 'local', chosen: 'small', source: 'default', models: [small, tamil], ...over });

const calls = (method) => apiFetch.mock.calls.filter(([, , , o]) => o?.method === method);
const bodyOf = (call) => JSON.parse(call[3].body);
const select = () => screen.findByRole('combobox', { name: 'Speech model' });

function mount(initial, answers = {}, props = {}) {
  apiFetch.mockImplementation(async (h, p, path, opts) => {
    const method = opts?.method ?? 'GET';
    const answer = answers[method];
    if (answer) {
      const out = await answer(path, opts);
      return out && 'ok' in out ? out : json(200, out);
    }
    return json(200, initial);
  });
  const showToast = vi.fn();
  render(<SpeechModelPicker theme={theme} apiHost="" apiPort="" showToast={showToast} voiceOn {...props} />);
  return showToast;
}

describe('SpeechModelPicker', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists the models with their languages and shows the one in use', async () => {
    mount(view({ chosen: 'whisper-small-ta', source: 'admin' }));
    expect((await select()).value).toBe('whisper-small-ta');
    expect(screen.getByRole('option', { name: 'Whisper small, Tamil · Tamil' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Whisper small' })).toBeTruthy();
  });

  it('says how the chosen model runs, its note and its licence', async () => {
    mount(view({ chosen: 'whisper-small-ta' }));
    const line = await screen.findByText(/converted on this server/);
    expect(line.textContent).toContain('Test it first.');
    expect(line.textContent).toContain('Licence: apache-2.0.');
    expect(line.textContent).toContain('Commercial use: yes.');
  });

  it('shows nothing for a remote engine', async () => {
    mount({ engine: 'openai', chosen: null, source: 'env', models: [] });
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    await new Promise((r) => { setTimeout(r); });
    expect(screen.queryByRole('combobox', { name: 'Speech model' })).toBeNull();
  });

  it('shows nothing if the list cannot be read', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<SpeechModelPicker theme={theme} apiHost="" apiPort="" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(screen.queryByRole('combobox', { name: 'Speech model' })).toBeNull();
  });

  it('saves a choice and says it is loading', async () => {
    const after = view({ chosen: 'whisper-small-ta', source: 'admin' });
    const toast = mount(view(), { PUT: () => after });
    fireEvent.change(await select(), { target: { value: 'whisper-small-ta' } });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(calls('PUT')[0][2]).toBe(`${LIST}/choice`);
    expect(bodyOf(calls('PUT')[0])).toEqual({ id: 'whisper-small-ta' });
    expect(toast.mock.calls[0][0]).toContain('Admin → Overview');
    expect((await select()).value).toBe('whisper-small-ta');
  });

  it('says a choice waits for Voice input when it is off', async () => {
    const after = view({ chosen: 'whisper-small-ta', source: 'admin' });
    const toast = mount(view(), { PUT: () => after }, { voiceOn: false });
    fireEvent.change(await select(), { target: { value: 'whisper-small-ta' } });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast.mock.calls[0][0]).toContain('when Voice input is on');
  });

  it('puts the old choice back when a save is refused, and reads the server reason', async () => {
    const toast = mount(view(), {
      PUT: () => json(422, { detail: { error: 'speech_model_cannot_run', message: 'Needs torch.' } }),
    });
    fireEvent.change(await select(), { target: { value: 'whisper-small-ta' } });
    await waitFor(() => expect(toast).toHaveBeenCalledWith("That didn't work: Needs torch.", 5000));
    expect((await select()).value).toBe('small');
  });

  it('disables a model that cannot run here and says why', async () => {
    mount(view({ models: [small, { ...tamil, why_not: 'Needs torch.' }] }));
    await select();
    expect(screen.getByRole('option', { name: 'Whisper small, Tamil · Tamil' }).disabled).toBe(true);
    expect(screen.getByText(/Some models can't run here\. Needs torch\./)).toBeTruthy();
  });

  it('offers the way back only for an admin choice', async () => {
    const after = view();
    mount(view({ source: 'admin' }), { PUT: () => after });
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting for the speech model' }));
    await waitFor(() => expect(bodyOf(calls('PUT')[0])).toEqual({ id: null }));
    await waitFor(() => expect(screen.queryByRole('button', { name: /server setting/ })).toBeNull());
  });

  it('names the server setting when that decided', async () => {
    mount(view({ source: 'env' }));
    expect(await screen.findByText('From the server setting SPEECH_MODEL.')).toBeTruthy();
  });

  it('adds a model and clears the form', async () => {
    const after = view({ models: [small, tamil, mine] });
    const toast = mount(view(), { POST: () => json(201, after) });
    await select();
    fireEvent.change(screen.getByLabelText('Repo'), { target: { value: ' owner/name ' } });
    fireEvent.change(screen.getByLabelText(/Language codes/), { target: { value: 'si, ta' } });
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add model' }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(bodyOf(calls('POST')[0])).toEqual({
      repo: 'owner/name', revision: null, languages: ['si', 'ta'], label: 'Mine', unconverted: false,
    });
    expect(screen.getByLabelText('Repo').value).toBe('');
    expect(screen.getByRole('option', { name: 'Mine' })).toBeTruthy();
  });

  it('sends the unconverted box and a revision', async () => {
    mount(view(), { POST: () => json(201, view()) });
    await select();
    fireEvent.change(screen.getByLabelText('Repo'), { target: { value: 'owner/name' } });
    fireEvent.change(screen.getByLabelText(/^Revision/), { target: { value: 'abc' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /without converting/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Add model' }));
    await waitFor(() => expect(calls('POST')).toHaveLength(1));
    expect(bodyOf(calls('POST')[0])).toMatchObject({ revision: 'abc', unconverted: true });
  });

  it('keeps what was typed and shows the reason when adding is refused', async () => {
    mount(view(), {
      POST: () => json(422, { detail: { error: 'not_whisper', message: "That repo isn't a Whisper model." } }),
    });
    await select();
    fireEvent.change(screen.getByLabelText('Repo'), { target: { value: 'owner/bert' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add model' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Whisper model');
    expect(screen.getByLabelText('Repo').value).toBe('owner/bert');
  });

  it('cannot add with an empty repo', async () => {
    mount(view());
    await select();
    expect(screen.getByRole('button', { name: 'Add model' }).disabled).toBe(true);
  });

  it('removes a model an admin added without choosing it first', async () => {
    const after = view();
    const toast = mount(view({ models: [small, tamil, mine] }), { DELETE: () => after });
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Mine' }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Removed Mine.', 5000));
    expect(calls('DELETE')[0][2]).toBe(`${LIST}/owner-name`);
    expect(calls('PUT')).toHaveLength(0);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Mine' })).toBeNull());
  });

  it('removes a model that can no longer run here', async () => {
    mount(view({ models: [small, { ...mine, why_not: 'Needs torch.' }] }), { DELETE: () => view() });
    await select();
    expect(screen.getByRole('option', { name: 'Mine' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Mine' }));
    await waitFor(() => expect(calls('DELETE')).toHaveLength(1));
  });

  it('offers no way to remove a model nobody added here', async () => {
    mount(view());
    await select();
    expect(screen.queryByRole('button', { name: /^Remove/ })).toBeNull();
    expect(screen.queryByText('Added here')).toBeNull();
  });

  it('keeps its name once the list is long enough to search', async () => {
    const models = Array.from({ length: 14 }, (_, i) => ({ ...small, id: `m-${i}`, label: `Model ${i}` }));
    mount(view({ chosen: 'm-0', models }));
    fireEvent.click(await select());
    expect(screen.getByRole('searchbox', { name: 'Find a speech model' })).toBeTruthy();
    expect(screen.getByRole('listbox', { name: 'Speech model' })).toBeTruthy();
  });
});
