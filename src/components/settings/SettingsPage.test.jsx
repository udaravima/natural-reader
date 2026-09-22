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
    inferenceSource: 'server', setInferenceSource: vi.fn(), chatTtsMode: 'streaming', setChatTtsMode: vi.fn(),
    chatAutoTts: true, setChatAutoTts: vi.fn(), inferenceByModel: {}, setInferenceByModel: vi.fn(),
    availableModels: [], selectedModel: '',
  },
  connectionSettings: {
    apiHost: '', setApiHost: vi.fn(), apiPort: '8000', setApiPort: vi.fn(),
    ollamaHost: '', setOllamaHost: vi.fn(), ollamaPort: '11434', setOllamaPort: vi.fn(),
    inferenceSource: 'server', backendAvailable: true,
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
});
