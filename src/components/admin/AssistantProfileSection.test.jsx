import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AssistantProfileSection } from './AssistantProfileSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const view = (over = {}) => ({
  text: '', source: 'none', fileConfigured: false, maxChars: 8000,
  preview: 'You are the assistant in Natural Reader…', warnings: [], ...over,
});

function mount(initial, onPut = (body) => view({ text: body.text, source: body.text ? 'admin' : 'none' })) {
  apiFetch.mockImplementation(async (host, port, path, opts) => {
    if (path === '/v1/admin/assistant' && opts?.method === 'PUT') return json(200, onPut(JSON.parse(opts.body)));
    if (path === '/v1/admin/assistant') return json(200, initial);
    return json(404, {});
  });
  const showToast = vi.fn();
  render(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={showToast} />);
  return showToast;
}

const puts = () => apiFetch.mock.calls.filter(([, , , o]) => o?.method === 'PUT');

describe('AssistantProfileSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows where the profile comes from and a size counter', async () => {
    mount(view({ text: 'From the file.', source: 'file', fileConfigured: true }));
    expect(await screen.findByText('From the deployment file')).toBeTruthy();
    expect(screen.getByLabelText('Assistant profile').value).toBe('From the file.');
    expect(screen.getByText(/14 \/ 8,000 characters · ≈ 4 tokens/)).toBeTruthy();
  });

  it('saves the edited text, and only when it changed', async () => {
    mount(view());
    const box = await screen.findByLabelText('Assistant profile');
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save.disabled).toBe(true);
    fireEvent.change(box, { target: { value: 'You are Ada.' } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ text: 'You are Ada.' });
    expect(await screen.findByText('Set here')).toBeTruthy();
  });

  it('resets to the deployment default by saving empty text', async () => {
    mount(view({ text: 'Be brief.', source: 'admin' }), () => view({ source: 'none' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reset to deployment default' }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][3].body)).toEqual({ text: '' });
    expect(await screen.findByText('None')).toBeTruthy();
  });

  it('shows the server warnings', async () => {
    mount(view({ text: 'Use web_search.', source: 'admin', warnings: ['It names web_search.'] }));
    expect(await screen.findByText('It names web_search.')).toBeTruthy();
  });

  it('opens a preview of what the model receives', async () => {
    mount(view({ preview: 'You are Ada.\n\nYou are working in Natural Reader…' }));
    fireEvent.click(await screen.findByText('What the model receives'));
    expect(screen.getByText(/You are working in Natural Reader/)).toBeTruthy();
  });

  it('fetches the preview again when refreshKey changes, and keeps what is being typed', async () => {
    let preview = 'Rules with the offer sentence.';
    apiFetch.mockImplementation(async () => json(200, view({ text: 'Be brief.', source: 'admin', preview })));
    const props = { theme, apiHost: '', apiPort: '', showToast: vi.fn() };
    const { rerender } = render(<AssistantProfileSection {...props} refreshKey={0} />);
    fireEvent.click(await screen.findByText('What the model receives'));
    expect(screen.getByText('Rules with the offer sentence.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Assistant profile'), { target: { value: 'Be brief. And kind.' } });
    expect(apiFetch).toHaveBeenCalledTimes(1);

    preview = 'Rules with web_search.';
    rerender(<AssistantProfileSection {...props} refreshKey={1} />);
    expect(await screen.findByText('Rules with web_search.')).toBeTruthy();
    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('Assistant profile').value).toBe('Be brief. And kind.');   // unsaved text survives
    expect(screen.getByRole('button', { name: 'Save' }).disabled).toBe(false);
  });

  it('does not fetch again while refreshKey stays the same', async () => {
    mount(view());
    await screen.findByLabelText('Assistant profile');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('keeps the old preview when a refresh fails', async () => {
    let ok = true;
    apiFetch.mockImplementation(async () => (ok ? json(200, view({ preview: 'First preview.' })) : json(500, {})));
    const props = { theme, apiHost: '', apiPort: '', showToast: vi.fn() };
    const { rerender } = render(<AssistantProfileSection {...props} refreshKey={0} />);
    fireEvent.click(await screen.findByText('What the model receives'));
    ok = false;
    rerender(<AssistantProfileSection {...props} refreshKey={1} />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
    expect(screen.getByText('First preview.')).toBeTruthy();
    expect(screen.queryByText(/Couldn't load the assistant profile/)).toBeNull();
  });

  it('says so when the profile cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    expect(await screen.findByText(/Couldn't load the assistant profile/)).toBeTruthy();
  });
});
