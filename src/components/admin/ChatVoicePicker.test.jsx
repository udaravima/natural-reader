import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatVoicePicker } from './ChatVoicePicker';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', border: '', text: '', textSecondary: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const ENGINES = [
  { id: 'kokoro', label: 'Kokoro on this server', available: true, needs: '' },
  { id: 'google', label: 'Google Gemini-TTS', available: true, needs: '' },
];
const VOICES = ['Charon', 'Kore', 'Puck'];
const view = (over = {}) => ({
  engine: 'kokoro', voice: null, source: 'default', engines: ENGINES, voices: VOICES,
  model: 'gemini-2.5-flash-tts', ...over,
});
const calls = (method) => apiFetch.mock.calls.filter(([, , , o]) => o?.method === method);
const bodyOf = (call) => JSON.parse(call[3].body);
const engineSelect = () => screen.findByRole('combobox', { name: 'Voice for the chat and Talk' });

function mount(initial, put) {
  apiFetch.mockImplementation(async (h, p, path, opts) => {
    if (opts?.method === 'PUT') {
      const out = await put(JSON.parse(opts.body));
      return out && 'ok' in out ? out : json(200, out);
    }
    return json(200, initial);
  });
  const showToast = vi.fn();
  render(<ChatVoicePicker theme={theme} apiHost="" apiPort="" showToast={showToast} />);
  return showToast;
}

describe('ChatVoicePicker', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows Kokoro in use, and that documents keep it whatever is chosen', async () => {
    mount(view());
    expect((await engineSelect()).value).toBe('kokoro');
    expect(screen.getByText(/documents and audiobooks keep Kokoro/i)).toBeTruthy();
    expect(screen.queryByRole('combobox', { name: "Google's voice" })).toBeNull();
  });

  it('a Google that isn\'t set up says what it needs', async () => {
    mount(view({ engines: [ENGINES[0], { ...ENGINES[1], available: false, needs: 'GOOGLE_TTS_MODEL' }], model: null }));
    const google = await screen.findByRole('option', { name: 'Google Gemini-TTS (needs GOOGLE_TTS_MODEL)' });
    expect(google.disabled).toBe(true);
  });

  it('switching to Google saves it, shows the voices and says where replies go', async () => {
    const showToast = mount(view(), (body) => view({ engine: 'google', voice: 'Kore', source: 'admin', ...body }));
    fireEvent.change(await engineSelect(), { target: { value: 'google' } });
    await waitFor(() => expect(calls('PUT').length).toBe(1));
    expect(calls('PUT')[0][2]).toBe('/v1/admin/chat-voice');
    expect(bodyOf(calls('PUT')[0])).toEqual({ engine: 'google' });
    expect((await screen.findByRole('combobox', { name: "Google's voice" })).value).toBe('Kore');
    expect(screen.getByText(/sent to Google Cloud/i)).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Google Gemini-TTS'), 5000);
  });

  it('choosing another voice keeps Google and sends the voice', async () => {
    mount(view({ engine: 'google', voice: 'Kore', source: 'admin' }), (body) => view({ ...body, source: 'admin' }));
    fireEvent.change(await screen.findByRole('combobox', { name: "Google's voice" }), { target: { value: 'Puck' } });
    await waitFor(() => expect(calls('PUT').length).toBe(1));
    expect(bodyOf(calls('PUT')[0])).toEqual({ engine: 'google', voice: 'Puck' });
  });

  it('a refusal keeps what was there and says why', async () => {
    const showToast = mount(view(), () => json(422, { detail: { message: "Google's voice isn't set up on this server" } }));
    fireEvent.change(await engineSelect(), { target: { value: 'google' } });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringContaining("isn't set up"), 5000));
    expect((await engineSelect()).value).toBe('kokoro');
  });

  it('goes back to the server setting', async () => {
    mount(view({ engine: 'google', voice: 'Kore', source: 'admin' }), () => view());
    fireEvent.click(await screen.findByRole('button', { name: 'Use the server setting for the chat voice' }));
    await waitFor(() => expect(calls('PUT').length).toBe(1));
    expect(bodyOf(calls('PUT')[0])).toEqual({ engine: null });
  });

  it('shows nothing if the setting cannot be read', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<ChatVoicePicker theme={theme} apiHost="" apiPort="" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(screen.queryByRole('combobox')).toBeNull();
  });
});
