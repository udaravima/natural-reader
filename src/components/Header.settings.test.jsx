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
  user: { email: 'me@x.io' }, onLogout: vi.fn(), onOpenSettings: vi.fn(),
  ...over,
});

describe('Header — settings + profile', () => {
  it('the gear button opens the settings dialog and leaves the view alone', () => {
    const setViewMode = vi.fn();
    const onOpenSettings = vi.fn();
    render(<Header {...baseProps({ setViewMode, onOpenSettings })} />);
    fireEvent.click(screen.getByRole('button', { name: /^settings$/i }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(setViewMode).not.toHaveBeenCalled();
  });

  it('renders a profile menu trigger', () => {
    render(<Header {...baseProps()} />);
    expect(screen.getByRole('button', { name: /account/i })).toBeInTheDocument();
  });

  // The narration keeps playing outside the reader, so it needs controls there.
  const held = { held: true, docName: 'book.pdf', currentPage: 3, numPages: 9, pausedForChat: false };

  it('outside the reader, a held narration shows its own pause, stop and way back', () => {
    const on = { handlePlayPause: vi.fn(), stopPlayback: vi.fn(), setViewMode: vi.fn() };
    render(<Header {...baseProps({ viewMode: 'chat', hasDocument: true, isPlaying: true, narration: held, ...on })} />);
    const group = screen.getByRole('group', { name: 'Narration' });
    expect(group).toHaveTextContent('book.pdf · p. 3/9');
    fireEvent.click(screen.getByRole('button', { name: 'Pause the narration' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop the narration' }));
    fireEvent.click(screen.getByTitle('Back to the page being read'));
    expect(on.handlePlayPause).toHaveBeenCalledTimes(1);
    expect(on.stopPlayback).toHaveBeenCalledTimes(1);
    expect(on.setViewMode).toHaveBeenCalledWith('reader');
  });

  it('says when the narration is paused for a chat reply', () => {
    render(<Header {...baseProps({ viewMode: 'library', hasDocument: true, narration: { ...held, pausedForChat: true } })} />);
    expect(screen.getByRole('group', { name: 'Narration' })).toHaveTextContent('Paused for the reply');
    expect(screen.getByRole('button', { name: 'Resume the narration' })).toBeInTheDocument();
  });

  it('shows no narration control in the reader (its own controls are there) or with none held', () => {
    const { rerender } = render(<Header {...baseProps({ viewMode: 'reader', hasDocument: true, narration: held })} />);
    expect(screen.queryByRole('group', { name: 'Narration' })).toBeNull();
    rerender(<Header {...baseProps({ viewMode: 'chat', hasDocument: true, narration: { ...held, held: false } })} />);
    expect(screen.queryByRole('group', { name: 'Narration' })).toBeNull();
  });
});
