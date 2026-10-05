import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../utils/apiFetch', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })),
}));

import SettingsPage from './SettingsPage';

const theme = {
  bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};

const bags = (over = {}) => ({
  theme,
  voiceSettings: {
    selectedVoice: 'af_bella', setSelectedVoice: vi.fn(), playbackSpeed: 1.0, setPlaybackSpeed: vi.fn(),
    volume: 1, setVolume: vi.fn(), isLocalhost: true, setIsLocalhost: vi.fn(),
    requestTimeout: 30, setRequestTimeout: vi.fn(), unlimitedBatchTimeout: false, setUnlimitedBatchTimeout: vi.fn(),
    isPreviewingVoice: false, previewVoice: vi.fn(), stopVoicePreview: vi.fn(), clearCache: vi.fn(),
  },
  chatSettings: {
    chatTtsMode: 'streaming', setChatTtsMode: vi.fn(),
    chatAutoTts: true, setChatAutoTts: vi.fn(), inferenceByModel: {}, setInferenceByModel: vi.fn(),
    availableModels: [], selectedModel: '',
  },
  connectionSettings: {
    apiHost: '', setApiHost: vi.fn(), apiPort: '8000', setApiPort: vi.fn(),
    backendAvailable: true,
  },
  appearanceSettings: {
    darkMode: false, setDarkMode: vi.fn(), layoutMode: 'auto', setLayoutMode: vi.fn(),
    mobileBreakpoint: 768, setMobileBreakpoint: vi.fn(),
    showHeaderControlsOnMobile: false, setShowHeaderControlsOnMobile: vi.fn(),
  },
  accountProps: { apiHost: '', apiPort: '8000', user: { email: 'me@x.io' } },
  ...over,
});

describe('SettingsPage — reader/global sections', () => {
  it('renders the five section headings', () => {
    render(<SettingsPage {...bags()} />);
    ['Voice & Reading', 'Chat & Inference', 'Connection', 'Appearance', 'Account']
      .forEach((h) => expect(screen.getByText(h)).toBeInTheDocument());
  });

  it('changing the speed calls setPlaybackSpeed', () => {
    const b = bags();
    const setPlaybackSpeed = vi.fn();
    b.voiceSettings.setPlaybackSpeed = setPlaybackSpeed;
    render(<SettingsPage {...b} />);
    fireEvent.change(screen.getByLabelText(/speed/i), { target: { value: '1.5' } });
    expect(setPlaybackSpeed).toHaveBeenCalledWith(1.5);
  });

  it('toggling dark mode calls setDarkMode', () => {
    const b = bags();
    const setDarkMode = vi.fn();
    b.appearanceSettings.setDarkMode = setDarkMode;
    render(<SettingsPage {...b} />);
    fireEvent.click(screen.getByLabelText(/dark mode/i));
    expect(setDarkMode).toHaveBeenCalled();
  });

  it("edits the chosen model's context window via the model selector", () => {
    const setInferenceByModel = vi.fn();
    const b = bags();
    b.chatSettings = {
      ...b.chatSettings,
      availableModels: [{ id: 'ollama:m1', provider: 'ollama', kind: 'ollama', name: 'm1', capabilities: {} },
        { id: 'ollama:m2', provider: 'ollama', kind: 'ollama', name: 'm2', capabilities: {} }],
      selectedModel: 'ollama:m1',
      inferenceByModel: { 'ollama:m1': {}, 'ollama:m2': {} }, setInferenceByModel,
    };
    render(<SettingsPage {...b} />);
    // The config picker defaults to the active model (m1).
    expect(screen.getByLabelText(/configuring model/i)).toHaveValue('ollama:m1');
    fireEvent.change(screen.getByLabelText(/context window/i), { target: { value: '8192' } });
    expect(setInferenceByModel).toHaveBeenCalled();
  });

  it('renders the Account panel (PAT management) in the Account section', async () => {
    render(<SettingsPage {...bags()} />);
    expect(await screen.findByPlaceholderText(/token name/i)).toBeInTheDocument();
  });
});

describe('SettingsPage — provider-aware inference settings', () => {
  const models = [
    { id: 'local:m1', provider: 'local', kind: 'ollama', name: 'm1', capabilities: { thinking: false } },
    { id: 'cloud:x', provider: 'cloud', kind: 'openai', name: 'x', capabilities: { thinking: true } },
  ];
  it('has no inference-source or Ollama host fields (Local mode is gone)', () => {
    render(<SettingsPage {...bags()} />);
    expect(screen.queryByText(/inference source/i)).toBeNull();
    expect(screen.queryByLabelText(/ollama host/i)).toBeNull();
  });
  it('shows a disabled "(unavailable)" option for the config picker when the saved model has no matching option', () => {
    const b = bags();
    b.chatSettings = { ...b.chatSettings, availableModels: models, selectedModel: 'local:missing' };
    render(<SettingsPage {...b} />);
    const select = screen.getByLabelText(/configuring model/i);
    expect(select).toHaveValue('local:missing');
    expect(screen.getByText('local:missing (unavailable)')).toBeInTheDocument();
  });

  it('shows context size and keep-alive only for Ollama models, thinking only when supported', () => {
    const b = bags();
    b.chatSettings = { ...b.chatSettings, availableModels: models, selectedModel: 'cloud:x' };
    render(<SettingsPage {...b} />);
    expect(screen.queryByLabelText(/context window/i)).toBeNull();
    expect(screen.queryByLabelText(/keep model warm/i)).toBeNull();
    expect(screen.getByLabelText(/thinking/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/configuring model/i), { target: { value: 'local:m1' } });
    expect(screen.getByLabelText(/context window/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/thinking/i)).toBeNull();
  });
});

describe('SettingsPage — deployment limits (v2.4 Task F)', () => {
  const model = {
    id: 'ollama:gemma4', provider: 'ollama', kind: 'ollama', name: 'gemma4', capabilities: {},
    limits: { numCtxMax: 16384, keepAliveMaxS: 1800 },
  };

  it('offers only what the server will honour and shows a saved value above it as capped', () => {
    const b = bags();
    b.chatSettings = { ...b.chatSettings, availableModels: [model], selectedModel: 'ollama:gemma4',
      inferenceByModel: { 'ollama:gemma4': { numCtx: 65536, keepAlive: -1 } } };
    render(<SettingsPage {...b} />);
    const ctx = screen.getByLabelText('Context window');
    expect([...ctx.options].map((o) => o.value)).not.toContain('32768');
    expect(ctx.value).toBe('16384');
    const ka = screen.getByLabelText('Keep model warm');
    expect([...ka.options].map((o) => o.value)).not.toContain('-1');
    expect(ka.value).toBe('30m');
  });
});
