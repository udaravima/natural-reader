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

  it('says so when the profile cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    expect(await screen.findByText(/Couldn't load the assistant profile/)).toBeTruthy();
  });
});
