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
// Reads of the profile itself (the section also asks once whether a helper model is set up).
const reads = () => apiFetch.mock.calls.filter(([, , p, o]) => p === '/v1/admin/assistant' && !o?.method);

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
    expect(reads()).toHaveLength(1);

    preview = 'Rules with web_search.';
    rerender(<AssistantProfileSection {...props} refreshKey={1} />);
    expect(await screen.findByText('Rules with web_search.')).toBeTruthy();
    expect(reads()).toHaveLength(2);
    expect(screen.getByLabelText('Assistant profile').value).toBe('Be brief. And kind.');   // unsaved text survives
    expect(screen.getByRole('button', { name: 'Save' }).disabled).toBe(false);
  });

  it('does not fetch again while refreshKey stays the same', async () => {
    mount(view());
    await screen.findByLabelText('Assistant profile');
    expect(reads()).toHaveLength(1);
  });

  it('keeps the old preview when a refresh fails', async () => {
    let ok = true;
    apiFetch.mockImplementation(async () => (ok ? json(200, view({ preview: 'First preview.' })) : json(500, {})));
    const props = { theme, apiHost: '', apiPort: '', showToast: vi.fn() };
    const { rerender } = render(<AssistantProfileSection {...props} refreshKey={0} />);
    fireEvent.click(await screen.findByText('What the model receives'));
    ok = false;
    rerender(<AssistantProfileSection {...props} refreshKey={1} />);
    await waitFor(() => expect(reads()).toHaveLength(2));
    expect(screen.getByText('First preview.')).toBeTruthy();
    expect(screen.queryByText(/Couldn't load the assistant profile/)).toBeNull();
  });

  it('says so when the profile cannot be loaded', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    render(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    expect(await screen.findByText(/Couldn't load the assistant profile/)).toBeTruthy();
  });

  // Helper spec §7: the helper model drafts a profile from a description; nothing is saved until Save.
  describe('Generate', () => {
    const DRAFT = 'You are Ada, a calm tutor.';
    function mountWith({ on = true, initial = view(), draft = () => json(200, { text: DRAFT, warnings: [] }) } = {}) {
      apiFetch.mockImplementation(async (host, port, path, opts) => {
        if (path === '/v1/settings/helper') return json(200, { on });
        if (path === '/v1/admin/assistant/draft') return draft(JSON.parse(opts.body));
        if (path === '/v1/admin/assistant') return json(200, initial);
        return json(404, {});
      });
      const showToast = vi.fn();
      render(<AssistantProfileSection theme={theme} apiHost="" apiPort="" showToast={showToast} />);
      return showToast;
    }
    const drafts = () => apiFetch.mock.calls.filter(([, , p]) => p === '/v1/admin/assistant/draft');
    const describe_ = async (text) => {
      fireEvent.change(await screen.findByLabelText('Describe the assistant'), { target: { value: text } });
      fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    };

    it('says where to turn the helper on when there is none', async () => {
      mountWith({ on: false });
      expect(await screen.findByText('Choose a helper model in Models to generate this.')).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Generate' })).toBeNull();
      expect(screen.queryByLabelText('Describe the assistant')).toBeNull();
    });

    it('puts the draft in the editor and saves nothing', async () => {
      const showToast = mountWith();
      expect((await screen.findByRole('button', { name: 'Generate' })).disabled).toBe(true);   // nothing described
      await describe_('A calm tutor called Ada');
      await waitFor(() => expect(screen.getByLabelText('Assistant profile').value).toBe(DRAFT));
      expect(JSON.parse(drafts()[0][3].body)).toEqual({ description: 'A calm tutor called Ada' });
      expect(puts()).toHaveLength(0);
      expect(screen.getByRole('button', { name: 'Save' }).disabled).toBe(false);
      expect(showToast).toHaveBeenCalledWith('Draft placed in the editor. Nothing is saved until you press Save.', 5000);
    });

    it('shows that it is writing, and takes no second request meanwhile', async () => {
      let finish;
      mountWith({ draft: () => new Promise((resolve) => { finish = () => resolve(json(200, { text: DRAFT, warnings: [] })); }) });
      await describe_('A calm tutor');
      const busy = await screen.findByRole('button', { name: 'Writing a draft…' });
      expect(busy.disabled).toBe(true);
      fireEvent.click(busy);
      expect(drafts()).toHaveLength(1);
      finish();
      expect(await screen.findByRole('button', { name: 'Generate' })).toBeTruthy();
    });

    it('asks before replacing unsaved text, and keeps it when told no', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
      mountWith({ initial: view({ text: 'Be brief.', source: 'admin' }) });
      fireEvent.change(await screen.findByLabelText('Assistant profile'), { target: { value: 'Be brief. And kind.' } });
      await describe_('A calm tutor');
      await waitFor(() => expect(confirm).toHaveBeenCalledWith('Replace the unsaved text in the editor with the draft?'));
      expect(screen.getByLabelText('Assistant profile').value).toBe('Be brief. And kind.');
      confirm.mockReturnValue(true);
      fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
      await waitFor(() => expect(screen.getByLabelText('Assistant profile').value).toBe(DRAFT));
      confirm.mockRestore();
    });

    it("shows the draft's warnings and the server's reason when it can't", async () => {
      const showToast = mountWith({ draft: () => json(200, { text: 'Use web_search.', warnings: ['It names web_search.'] }) });
      await describe_('A searcher');
      expect(await screen.findByText('It names web_search.')).toBeTruthy();
      apiFetch.mockImplementation(async (host, port, path) => (path === '/v1/admin/assistant/draft'
        ? json(429, { detail: { error: 'budget_exhausted', message: 'Daily inference budget exhausted.' } })
        : json(200, { on: true })));
      fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
      await waitFor(() => expect(showToast).toHaveBeenCalledWith('Daily inference budget exhausted.', 5000));
      expect(screen.getByLabelText('Assistant profile').value).toBe('Use web_search.');
    });
  });
});
