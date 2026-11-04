import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { GoogleRecognizers } from './GoogleRecognizers';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', border: '', text: '', textSecondary: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const URL = '/v1/admin/google-recognizers';
const SI = 'projects/p-1/locations/asia-southeast1/recognizers/asr-si';
const TA = 'projects/p-1/locations/asia-southeast1/recognizers/asr-ta-en';
const OLD = 'projects/p-1/locations/global/recognizers/asr-si-ta';
const row = (name, id, model, languages, chosen = false) => ({
  name, id, project: 'p-1', location: name.split('/')[3], model, codes: [], languages, chosen,
});
const si = row(SI, 'asr-si', 'chirp_2', [{ code: 'si', name: 'Sinhala' }]);
const ta = row(TA, 'asr-ta-en', 'chirp_3', [{ code: 'ta', name: 'Tamil' }, { code: 'en', name: 'English' }]);
const listing = (over = {}) => ({ recognizers: [ta, si], chosen: [], missing: [], problems: [], source: 'none', ...over });
const puts = () => apiFetch.mock.calls.filter(([, , , o]) => o?.method === 'PUT');
const sent = (call) => JSON.parse(call[3].body).names;

function mount(list, { onSaved = vi.fn(), put } = {}) {
  apiFetch.mockImplementation(async (h, p, path, opts) => {
    if (opts?.method === 'PUT') {
      const names = JSON.parse(opts.body).names;
      return put ? put(names) : json(200, { google: { offered: true, chosen: names, source: names.length ? 'admin' : 'none' } });
    }
    return json(200, list);
  });
  const showToast = vi.fn();
  render(<GoogleRecognizers theme={theme} apiHost="" apiPort="" showToast={showToast} onSaved={onSaved} />);
  return { onSaved, showToast };
}

const boxes = () => screen.getAllByRole('checkbox');

describe('GoogleRecognizers', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists what Google holds: id, model, languages and location', async () => {
    mount(listing());
    expect(await screen.findByRole('checkbox', { name: /asr-ta-en · chirp_3 · Tamil, English · asia-southeast1/ })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /asr-si · chirp_2 · Sinhala · asia-southeast1/ })).toBeTruthy();
    expect(apiFetch.mock.calls[0][2]).toBe(URL);
  });

  it('ticking adds to the end of the order and tells the picker', async () => {
    const { onSaved } = mount(listing({ recognizers: [{ ...ta, chosen: true }, si], chosen: [TA], source: 'admin' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /asr-si ·/ }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(sent(puts()[0])).toEqual([TA, SI]);
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(screen.getByRole('checkbox', { name: /asr-si ·/ }).checked).toBe(true);
  });

  it('the first ticked hears Auto, and Move up changes the order', async () => {
    mount(listing({ recognizers: [{ ...ta, chosen: true }, { ...si, chosen: true }], chosen: [TA, SI], source: 'admin' }));
    const first = (await screen.findAllByRole('listitem'))[0];
    expect(within(first).getByText(/Auto/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Move asr-si up' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(sent(puts()[0])).toEqual([SI, TA]);
    await waitFor(() => expect(within(screen.getAllByRole('listitem')[0]).getByText(/asr-si ·/)).toBeTruthy());
  });

  it('unticking the last one says the server setting applies', async () => {
    mount(listing({ recognizers: [{ ...si, chosen: true }, ta], chosen: [SI], source: 'admin' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /asr-si ·/ }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(sent(puts()[0])).toEqual([]);
  });

  it('says what Google refused, in its words, and a ticked one it no longer lists', async () => {
    mount(listing({ recognizers: [], chosen: [OLD], missing: [OLD], source: 'admin',
      problems: ['Google refused to list recognizers in p-1, eu: 403: Permission denied'] }));
    expect(await screen.findByText(/Permission denied/)).toBeTruthy();
    expect(screen.getByText(/asr-si-ta/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Untick asr-si-ta' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(sent(puts()[0])).toEqual([]);
  });

  it('a refusal puts the ticks back and says why', async () => {
    const { showToast } = mount(listing({ recognizers: [ta, si] }), {
      put: async () => json(422, { detail: { error: 'unknown_recognizer', message: "Google doesn't list asr-si." } }),
    });
    fireEvent.click(await screen.findByRole('checkbox', { name: /asr-si ·/ }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringContaining("Google doesn't list asr-si."), 5000));
    expect(boxes().every((b) => !b.checked)).toBe(true);
  });

  it('with nothing ticked, names the server setting in use', async () => {
    mount(listing({ chosen: [], source: 'env', in_use: [OLD] }));
    expect((await screen.findByText(/GOOGLE_SPEECH_RECOGNIZER/)).textContent).toContain('asr-si-ta');
  });
});
