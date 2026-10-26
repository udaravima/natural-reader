import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PeopleSection } from './PeopleSection';
import { AdminProjectsSection } from './AdminProjectsSection';
import { AssistantProfileSection } from './AssistantProfileSection';
import { ChatOptionsSection } from './ChatOptionsSection';
import { DefaultsSection } from './DefaultsSection';
import { UsageSection } from './UsageSection';
import { ModelsSection } from './ModelsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

// jsdom has no layout, so a pixel size can't be measured here. This guards the class that
// makes it 44 px on a phone: every control carries `min-h-11` itself, or sits inside a
// label that does (a checkbox or radio is tapped through its label).

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const props = { theme, apiHost: '', apiPort: '', showToast: vi.fn() };

const BUILT_IN = { chatAutoTts: false, chatTtsMode: 'after-complete', think: 'off', selectedVoice: 'af_heart', playbackSpeed: 1 };
const person = (over) => ({
  id: 'u1', email: 'a@x.io', role: 'member', status: 'pending', oidc_sub: null, capabilities: [],
  inference_daily_token_budget: null, project_limit: null, created_at: '2026-09-17T00:00:00Z', ...over,
});
const project = (over) => ({ id: 'p1', name: 'Orphan', member_count: 0, doc_count: 1, owners: [], ownerless: true, ...over });

const USAGE_ROW = (over) => ({
  prompt_tokens: 1, eval_tokens: 2, tokens: 3, requests: 1, chat_tokens: 3, reader_tokens: 0, estimated_tokens: 0, ...over,
});

// Minimal answers for every route these sections call; a section ignores the ones it doesn't use.
const ROUTES = {
  '/v1/admin/users': [person({ id: 'u1' }), person({ id: 'u2', email: 'b@x.io', status: 'active' })],
  '/v1/admin/projects': [project()],
  '/v1/admin/assistant': { text: 'Be brief.', source: 'admin', maxChars: 8000, preview: 'Preview', warnings: [] },
  '/v1/admin/chat-options': { web_search: 'ask', source: 'admin', searxng: true },
  '/v1/admin/defaults': {
    values: { ...BUILT_IN }, builtIn: BUILT_IN, overridden: [], epochs: {},
    choices: { chatTtsMode: ['streaming', 'after-complete'], think: ['off', 'on'] },
  },
  '/v1/admin/settings/models': {
    settings: {
      reader: { value: 'chat', source: 'default', env: 'READER_MODEL', fallback: 'chat', fallback_source: 'default' },
      context_size: { value: 8192, source: 'admin', env: 'INFERENCE_LOCAL_NUM_CTX', fallback: null, fallback_source: 'default' },
      context_sizes: { value: {}, source: 'admin', env: null, fallback: {}, fallback_source: 'default' },
      context_windows: { value: {}, source: 'admin', env: null, fallback: {}, fallback_source: 'default' },
      provider_locality: { value: { ollama: 'cloud' }, source: 'admin', env: null, fallback: {}, fallback_source: 'default' },
      chat_models: { value: [], source: 'default', env: null, fallback: [], fallback_source: 'default' },
    },
    info: [],
  },
  '/v1/admin/models': {
    providers: [{ name: 'ollama', kind: 'ollama', host: 'ollama:11434', detected: 'local', reachable: true }],
    models: [
      { id: 'ollama:a', provider: 'ollama', kind: 'ollama', name: 'a', reported_window: null, cloud_name: false },
      { id: 'ollama:b', provider: 'ollama', kind: 'ollama', name: 'b', reported_window: 4096, cloud_name: false },
    ],
    limits: { num_ctx_max: 32768 },
  },
  '/v1/admin/inference/usage?days=7&group=model': [USAGE_ROW({ model: 'ollama:a', tag: 'local' })],
  '/v1/admin/inference/usage?days=7&group=user': [USAGE_ROW({ user_id: 'u1', email: 'a@x.io' })],
  '/v1/admin/inference/usage?days=7&group=day': [USAGE_ROW({ day: '2026-10-25' })],
  '/v1/admin/inference/usage?days=7&group=model&user_id=u1': [USAGE_ROW({ model: 'ollama:a', tag: 'local' })],
};

const SECTIONS = {
  People: { ui: <PeopleSection {...props} currentUserId="me" />, ready: 'Enroll email' },
  Projects: { ui: <AdminProjectsSection {...props} currentUserId="me" onOpenProject={vi.fn()} />, ready: 'Open Orphan' },
  Assistant: { ui: <AssistantProfileSection {...props} />, ready: 'Assistant profile' },
  Chat: { ui: <ChatOptionsSection {...props} />, ready: 'Ask first' },
  'Defaults for users': { ui: <DefaultsSection {...props} />, ready: 'Voice' },
  Models: { ui: <ModelsSection {...props} />, ready: 'Reader' },
  Usage: { ui: <UsageSection {...props} />, ready: 'Usage days' },
};

const hasClass = (el) => el.classList.contains('min-h-11');
const tappable = (el) => hasClass(el) || hasClass(el.closest('label') ?? document.createElement('i'));

const CONTROLS = 'button, input, select, textarea, summary';
const unsized = (container) =>
  [...container.querySelectorAll(CONTROLS)]
    .filter((el) => !tappable(el))
    .map((el) => el.getAttribute('aria-label') || el.textContent || el.outerHTML.slice(0, 60));

// The other half: from md up the look stays as it was. Anything sized for a phone gives the size back
// at md, and a summary keeps its default display (a display utility on it removes the disclosure triangle).
const DISPLAY = /^(max-\w+:)?(flex|inline-flex|grid|inline-grid|block|inline-block|contents|hidden)$/;
const desktopChanged = (container) => [
  ...[...container.querySelectorAll('.min-h-11')]
    .filter((el) => !el.classList.contains('md:min-h-0'))
    .map((el) => `min-h-11 without md:min-h-0: ${el.outerHTML.slice(0, 60)}`),
  ...[...container.querySelectorAll('summary')]
    .filter((el) => [...el.classList].some((c) => DISPLAY.test(c)))
    .map((el) => `summary with a display utility: ${el.textContent}`),
];

describe('Admin console: 44 px tap targets below md', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockImplementation(async (h, p, path) => {
      // People asks for a page: /v1/admin/users?limit=50&offset=0
      const key = path.startsWith('/v1/admin/users?') ? '/v1/admin/users' : path;
      return key in ROUTES ? json(200, ROUTES[key]) : json(404, {});
    });
  });

  for (const [name, { ui, ready }] of Object.entries(SECTIONS)) {
    it(`${name}: every control has min-h-11`, async () => {
      const { container } = render(ui);
      await screen.findByLabelText(ready);
      expect(container.querySelectorAll('button, input, select, textarea, summary').length).toBeGreaterThan(0);
      expect(unsized(container)).toEqual([]);
      expect(desktopChanged(container)).toEqual([]);
    });
  }

  it('Usage: the view toggles, Model filter and a person disclosure too', async () => {
    const { container } = render(SECTIONS.Usage.ui);
    await screen.findByText('ollama:a');
    expect(screen.getAllByRole('button').length).toBe(3);   // By model, By person, By day
    fireEvent.click(screen.getByRole('button', { name: 'By person' }));
    await screen.findByLabelText('Models for a@x.io');
    expect(screen.getByLabelText('Model')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Models for a@x.io'));
    await waitFor(() => expect(container.querySelectorAll('table').length).toBe(2));
    expect(unsized(container)).toEqual([]);
    expect(desktopChanged(container)).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'By day' }));
    await screen.findByText('2026-10-25');
    expect(unsized(container)).toEqual([]);
    expect(desktopChanged(container)).toEqual([]);
  });

  it('People: the delete confirmation controls too', async () => {
    const { container } = render(SECTIONS.People.ui);
    await screen.findByLabelText('Enroll email');
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    expect(screen.getByLabelText('Confirm email')).toBeTruthy();
    expect(unsized(container)).toEqual([]);
    expect(desktopChanged(container)).toEqual([]);
  });

  it('People: Show more too, which appears only while the list is longer than what is loaded', async () => {
    apiFetch.mockImplementation(async (h, p, path) => (path.startsWith('/v1/admin/users?')
      ? { ...json(200, ROUTES['/v1/admin/users']), headers: { get: () => '120' } }
      : json(404, {})));
    const { container } = render(SECTIONS.People.ui);
    await screen.findByRole('button', { name: 'Show more' });
    expect(unsized(container)).toEqual([]);
    expect(desktopChanged(container)).toEqual([]);
  });

  it('Defaults: the "Apply to everyone" confirmation too', async () => {
    const { container } = render(SECTIONS['Defaults for users'].ui);
    await screen.findByLabelText('Voice');
    fireEvent.click(screen.getByRole('button', { name: /apply voice to everyone/i }));
    expect(screen.getByRole('button', { name: 'Replace for everyone' })).toBeTruthy();
    expect(unsized(container)).toEqual([]);
    expect(desktopChanged(container)).toEqual([]);
  });

  it('Projects: the Retry button of a failed load too', async () => {
    apiFetch.mockImplementation(async () => json(500, {}));
    const { container } = render(SECTIONS.Projects.ui);
    await screen.findByRole('button', { name: 'Retry' });
    expect(unsized(container)).toEqual([]);
    expect(desktopChanged(container)).toEqual([]);
  });
});
