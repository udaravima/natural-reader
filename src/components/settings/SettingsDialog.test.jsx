import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../utils/apiFetch', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })),
}));

import SettingsDialog from './SettingsDialog';

const theme = {
  bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};

const bags = (over = {}) => ({
  theme, tab: 'voice', onTab: vi.fn(), onClose: vi.fn(),
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

describe('SettingsDialog — a dialog over the current view', () => {
  it('renders nothing while closed', () => {
    const { container } = render(<SettingsDialog {...bags({ tab: null })} />);
    expect(container.innerHTML).toBe('');
  });

  it('is a modal dialog with one tab per section, and no Profile tab without a profile', () => {
    render(<SettingsDialog {...bags()} />);
    expect(screen.getByRole('dialog', { name: 'Settings' })).toHaveAttribute('aria-modal', 'true');
    expect(screen.getAllByRole('tab').map((t) => t.textContent))
      .toEqual(['Voice & reading', 'Chat', 'Appearance', 'Connection', 'Access tokens']);
    expect(screen.getByRole('tab', { name: 'Voice & reading' })).toHaveAttribute('aria-selected', 'true');
  });

  it('shows the Profile tab first when given a profile panel', () => {
    render(<SettingsDialog {...bags({ tab: 'profile', profile: <p>my profile</p> })} />);
    expect(screen.getAllByRole('tab')[0]).toHaveTextContent('Profile');
    expect(screen.getByText('my profile')).toBeInTheDocument();
  });

  it('a tab click and the arrow keys switch sections', () => {
    const onTab = vi.fn();
    render(<SettingsDialog {...bags({ onTab })} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Chat' }));
    expect(onTab).toHaveBeenLastCalledWith('chat');
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Voice & reading' }), { key: 'ArrowDown' });
    expect(onTab).toHaveBeenLastCalledWith('chat');
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Voice & reading' }), { key: 'ArrowUp' });
    expect(onTab).toHaveBeenLastCalledWith('tokens');
  });

  it('Escape, the close button and the backdrop close it; a click inside does not', () => {
    const onClose = vi.fn();
    const { container } = render(<SettingsDialog {...bags({ onClose })} />);
    fireEvent.mouseDown(screen.getByLabelText('Speed'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByLabelText('Speed'), { key: 'Escape' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Close settings' })[0]);
    fireEvent.mouseDown(container.firstChild);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('a click on blank space inside keeps focus in the dialog', () => {
    render(<SettingsDialog {...bags()} />);
    const box = screen.getByRole('dialog');
    expect(box).toHaveAttribute('tabindex', '-1');
    box.focus();
    expect(document.activeElement).toBe(box);
  });

  it('gives focus back to what opened it', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { rerender } = render(<SettingsDialog {...bags()} />);
    expect(document.activeElement).toHaveTextContent('Voice & reading');
    rerender(<SettingsDialog {...bags({ tab: null })} />);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('changing the speed calls setPlaybackSpeed', () => {
    const b = bags();
    const setPlaybackSpeed = vi.fn();
    b.voiceSettings.setPlaybackSpeed = setPlaybackSpeed;
    render(<SettingsDialog {...b} />);
    fireEvent.change(screen.getByLabelText(/speed/i), { target: { value: '1.5' } });
    expect(setPlaybackSpeed).toHaveBeenCalledWith(1.5);
  });

  it('toggling dark mode calls setDarkMode', () => {
    const b = bags({ tab: 'appearance' });
    const setDarkMode = vi.fn();
    b.appearanceSettings.setDarkMode = setDarkMode;
    render(<SettingsDialog {...b} />);
    fireEvent.click(screen.getByLabelText(/dark mode/i));
    expect(setDarkMode).toHaveBeenCalled();
  });

  it("edits the chosen model's context window via the model selector", () => {
    const setInferenceByModel = vi.fn();
    const b = bags({ tab: 'chat' });
    b.chatSettings = {
      ...b.chatSettings,
      availableModels: [{ id: 'ollama:m1', provider: 'ollama', kind: 'ollama', name: 'm1', capabilities: {} },
        { id: 'ollama:m2', provider: 'ollama', kind: 'ollama', name: 'm2', capabilities: {} }],
      selectedModel: 'ollama:m1',
      inferenceByModel: { 'ollama:m1': {}, 'ollama:m2': {} }, setInferenceByModel,
    };
    render(<SettingsDialog {...b} />);
    // The config picker defaults to the active model (m1).
    expect(screen.getByLabelText(/configuring model/i)).toHaveValue('ollama:m1');
    fireEvent.change(screen.getByLabelText(/context window/i), { target: { value: '8192' } });
    expect(setInferenceByModel).toHaveBeenCalled();
  });

  it('renders the Account panel (PAT management) in the Access tokens tab', async () => {
    render(<SettingsDialog {...bags({ tab: 'tokens' })} />);
    expect(await screen.findByPlaceholderText(/token name/i)).toBeInTheDocument();
  });
});

describe('SettingsDialog — provider-aware inference settings', () => {
  const models = [
    { id: 'local:m1', provider: 'local', kind: 'ollama', name: 'm1', capabilities: { thinking: false } },
    { id: 'cloud:x', provider: 'cloud', kind: 'openai', name: 'x', capabilities: { thinking: true } },
  ];
  it('has no inference-source or Ollama host fields (Local mode is gone)', () => {
    render(<SettingsDialog {...bags({ tab: 'chat' })} />);
    expect(screen.queryByText(/inference source/i)).toBeNull();
    expect(screen.queryByLabelText(/ollama host/i)).toBeNull();
  });
  it('shows a disabled "(unavailable)" option for the config picker when the saved model has no matching option', () => {
    const b = bags({ tab: 'chat' });
    b.chatSettings = { ...b.chatSettings, availableModels: models, selectedModel: 'local:missing' };
    render(<SettingsDialog {...b} />);
    const select = screen.getByLabelText(/configuring model/i);
    expect(select).toHaveValue('local:missing');
    expect(screen.getByText('local:missing (unavailable)')).toBeInTheDocument();
  });

  it('shows context size and keep-alive only for Ollama models, thinking only when supported', () => {
    const b = bags({ tab: 'chat' });
    b.chatSettings = { ...b.chatSettings, availableModels: models, selectedModel: 'cloud:x' };
    render(<SettingsDialog {...b} />);
    expect(screen.queryByLabelText(/context window/i)).toBeNull();
    expect(screen.queryByLabelText(/keep model warm/i)).toBeNull();
    expect(screen.getByLabelText(/thinking/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/configuring model/i), { target: { value: 'local:m1' } });
    expect(screen.getByLabelText(/context window/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/thinking/i)).toBeNull();
  });
});

describe('SettingsDialog — deployment limits (v2.4 Task F)', () => {
  const model = {
    id: 'ollama:gemma4', provider: 'ollama', kind: 'ollama', name: 'gemma4', capabilities: {},
    limits: { numCtxMax: 16384, keepAliveMaxS: 1800 },
  };

  it('offers only what the server will honour and shows a saved value above it as capped', () => {
    const b = bags({ tab: 'chat' });
    b.chatSettings = { ...b.chatSettings, availableModels: [model], selectedModel: 'ollama:gemma4',
      inferenceByModel: { 'ollama:gemma4': { numCtx: 65536, keepAlive: -1 } } };
    render(<SettingsDialog {...b} />);
    const ctx = screen.getByLabelText('Context window');
    expect([...ctx.options].map((o) => o.value)).not.toContain('32768');
    expect(ctx.value).toBe('16384');
    const ka = screen.getByLabelText('Keep model warm');
    expect([...ka.options].map((o) => o.value)).not.toContain('-1');
    expect(ka.value).toBe('30m');
  });
});

describe('SettingsDialog — the context size the server will use (admin console v2, Task 8)', () => {
  const local = (contextSize) => ({
    id: 'ollama:gemma4', provider: 'ollama', kind: 'ollama', name: 'gemma4', capabilities: {},
    limits: { numCtxMax: 32768, keepAliveMaxS: null },
    ...(contextSize ? { context_size: contextSize } : {}),
  });
  const open = (model, stored = {}) => {
    const b = bags({ tab: 'chat' });
    b.chatSettings = { ...b.chatSettings, availableModels: [model], selectedModel: model.id,
      inferenceByModel: { [model.id]: stored } };
    render(<SettingsDialog {...b} />);
    return b;
  };

  it('disables the row and says the admin set it when the size is fixed, keeping the stored value', () => {
    const b = open(local({ fixed: 16384, auto: 16384 }), { numCtx: 8192 });
    const ctx = screen.getByLabelText('Context window');
    expect(ctx).toBeDisabled();
    expect(screen.getByText('Set by your admin: 16,384 tokens.')).toBeInTheDocument();
    expect(ctx).toHaveAccessibleDescription('Set by your admin: 16,384 tokens.');
    expect(b.chatSettings.setInferenceByModel).not.toHaveBeenCalled();
  });

  it('a locked row shows the admin\'s size, not the stored one, and saving another knob keeps the stored 8192 (P13)', () => {
    const b = open(local({ fixed: 12288, auto: 16384 }), { numCtx: 8192 });
    const ctx = screen.getByLabelText('Context window');
    expect(ctx).toBeDisabled();
    expect(ctx.options[ctx.selectedIndex].textContent).toBe('12,288');
    expect(screen.getByText('Set by your admin: 12,288 tokens.')).toBeInTheDocument();
    expect(b.chatSettings.setInferenceByModel).not.toHaveBeenCalled();
    // another knob saves through the same per-model patch: the stored context value rides along untouched
    fireEvent.change(screen.getByLabelText('Max reply tokens'), { target: { value: '1024' } });
    const update = b.chatSettings.setInferenceByModel.mock.calls.at(-1)[0];
    const next = typeof update === 'function' ? update({ 'ollama:gemma4': { numCtx: 8192 } }) : update;
    expect(next['ollama:gemma4']).toMatchObject({ numCtx: 8192, numPredict: 1024 });
  });

  it('a locked row with no stored value shows the admin\'s size too, never "Auto"', () => {
    open(local({ fixed: 20000, auto: 16384 }));
    const ctx = screen.getByLabelText('Context window');
    expect(ctx.options[ctx.selectedIndex].textContent).toBe('20,000');
  });

  it('formats a larger fixed size with a thousands separator', () => {
    open(local({ fixed: 32768, auto: 16384 }));
    expect(screen.getByText('Set by your admin: 32,768 tokens.')).toBeInTheDocument();
  });

  it('names the size Auto resolves to when nothing is fixed, and the row stays usable', () => {
    open(local({ fixed: null, auto: 16384 }));
    const ctx = screen.getByLabelText('Context window');
    expect(ctx).not.toBeDisabled();
    expect([...ctx.options].find((o) => o.value === 'auto').textContent).toBe('Auto (16,384 on this server)');
    expect(screen.queryByText(/Set by your admin/)).toBeNull();
  });

  it('leaves the row as it was for a model without context_size', () => {
    open(local(null));
    const ctx = screen.getByLabelText('Context window');
    expect(ctx).not.toBeDisabled();
    expect([...ctx.options].find((o) => o.value === 'auto').textContent).toBe('Auto');
    expect(screen.queryByText(/Set by your admin/)).toBeNull();
  });
});

