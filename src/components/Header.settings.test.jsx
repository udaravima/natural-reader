import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import Header from './Header';

const theme = {
  bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
  theme, darkMode: false, hasDocument: false, status: '', isPlaying: false,
  isLocalhost: true, setIsLocalhost: vi.fn(), isDownloading: false, textItems: [],
  showHeaderControlsOnMobile: false, sidebarOpen: false, setSidebarOpen: vi.fn(),
  showShortcuts: false, setShowShortcuts: vi.fn(), fileInputRef: { current: null },
  viewMode: 'reader', setViewMode: vi.fn(), isAdmin: false, canReader: true, canChat: true,
  handlePlayPause: vi.fn(), stopPlayback: vi.fn(), skipToNextSentence: vi.fn(), skipToPrevSentence: vi.fn(),
  setDarkMode: vi.fn(), downloadPageAudio: vi.fn(), handleFileUpload: vi.fn(),
  calculateEstimatedTimeRemaining: () => null, playbackSpeed: 1.0,
  onGoHome: vi.fn(), onDownloadBookAudio: vi.fn(), bookProgress: null, onCancelBookDownload: vi.fn(),
  onEnterDistractionFree: vi.fn(), workspaceName: null, onCloseWorkspace: vi.fn(),
  user: { email: 'me@x.io' }, onLogout: vi.fn(),
  ...over,
});

describe('Header — settings + profile', () => {
  it('the gear button switches to the settings view', () => {
    const setViewMode = vi.fn();
    render(<Header {...baseProps({ setViewMode })} />);
    fireEvent.click(screen.getByRole('button', { name: /^settings$/i }));
    expect(setViewMode).toHaveBeenCalledWith('settings');
  });

  it('renders a profile menu trigger', () => {
    render(<Header {...baseProps()} />);
    expect(screen.getByRole('button', { name: /account/i })).toBeInTheDocument();
  });
});
