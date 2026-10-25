import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { OverviewSection } from './OverviewSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const labels = { documents: 'Documents', chat: 'Chat', deployment: 'Deployment' };
const sectionLabel = (id) => labels[id];

const check = (over = {}) => ({
  id: 'providers', state: 'ok', title: 'Model providers', summary: '1 provider answers.', section: null, ...over,
});

function mount(checks, onOpenSection = vi.fn()) {
  apiFetch.mockResolvedValue(json(200, { checks }));
  render(
    <OverviewSection theme={theme} apiHost="" apiPort="" sectionLabel={sectionLabel} onOpenSection={onOpenSection} />
  );
  return onOpenSection;
}

const overviewCalls = () => apiFetch.mock.calls.filter(([, , path]) => path === '/v1/admin/overview');

describe('OverviewSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows each check with a state word, and links to the section that fixes it', async () => {
    const onOpen = mount([
      check(),
      check({ id: 'docling', state: 'error', title: 'Docling', summary: 'Docling is not installed.', section: 'documents' }),
      check({ id: 'web_search', state: 'off', title: 'Web search', summary: 'Not set up.' }),
      check({ id: 'reader', state: 'warn', title: 'Reader', summary: 'Still the fallback.', section: 'deployment' }),
    ]);
    const docling = (await screen.findByText('Docling is not installed.')).closest('li');
    expect(within(docling).getByText('Problem')).toBeInTheDocument();
    expect(within(docling).getByText('Docling')).toBeInTheDocument();
    fireEvent.click(within(docling).getByRole('button', { name: 'Open Documents' }));
    expect(onOpen).toHaveBeenCalledWith('documents');

    expect(screen.getByText('OK')).toBeInTheDocument();
    expect(screen.getByText('Off')).toBeInTheDocument();
    expect(screen.getByText('Warning')).toBeInTheDocument();
    // A check with no section has no link.
    expect(within(screen.getByText('Not set up.').closest('li')).queryByRole('button')).toBeNull();
    expect(screen.getByRole('button', { name: 'Open Deployment' })).toBeInTheDocument();
  });

  it('lists each model in the context check', async () => {
    mount([check({
      id: 'context', state: 'warn', title: 'Context windows', summary: 'One window is unknown.', section: 'chat',
      items: [
        { model: 'ollama:m', window: null, source: 'Unknown: the provider does not say.', enough: false },
        { model: 'ollama:big', window: 131072, source: 'Ollama, loaded now.', enough: true },
      ],
    })]);
    const table = await screen.findByRole('table');
    const row = within(table).getByText('ollama:m').closest('tr');
    expect(within(row).getByText('Unknown', { exact: true })).toBeInTheDocument();
    expect(within(row).getByText('Unknown: the provider does not say.')).toBeInTheDocument();
    expect(within(row).getByText('No')).toBeInTheDocument();
    const big = within(table).getByText('ollama:big').closest('tr');
    expect(within(big).getByText('131,072 tokens')).toBeInTheDocument();
    expect(within(big).getByText('Yes')).toBeInTheDocument();
    for (const h of ['Model', 'Window', 'Where it came from', 'Room']) {
      expect(within(table).getByRole('columnheader', { name: h })).toBeInTheDocument();
    }
  });

  it('says it is checking while the first answer is out', async () => {
    let answer;
    apiFetch.mockReturnValue(new Promise((r) => { answer = r; }));
    render(<OverviewSection theme={theme} apiHost="" apiPort="" sectionLabel={sectionLabel} onOpenSection={vi.fn()} />);
    expect(screen.getByText('Checking…')).toBeInTheDocument();
    answer(json(200, { checks: [check()] }));
    expect(await screen.findByText('1 provider answers.')).toBeInTheDocument();
    expect(screen.queryByText('Checking…')).toBeNull();
  });

  it('checks again on request', async () => {
    mount([check()]);
    await screen.findByText('1 provider answers.');
    expect(overviewCalls()).toHaveLength(1);
    apiFetch.mockResolvedValue(json(200, { checks: [check({ summary: 'Now 2 providers answer.' })] }));
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('Now 2 providers answer.')).toBeInTheDocument();
    expect(overviewCalls()).toHaveLength(2);
  });

  it("says so when the checks couldn't run, and offers another try", async () => {
    apiFetch.mockResolvedValueOnce(json(500, {}));
    render(<OverviewSection theme={theme} apiHost="" apiPort="" sectionLabel={sectionLabel} onOpenSection={vi.fn()} />);
    expect(await screen.findByText("Couldn't run the checks.")).toBeInTheDocument();
    apiFetch.mockResolvedValue(json(200, { checks: [check()] }));
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('1 provider answers.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Couldn't run the checks.")).toBeNull());
  });
});
