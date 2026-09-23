import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import Sidebar from './Sidebar';

const theme = {
  bg: '', bgSecondary: '', border: '', borderSecondary: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};

// hasDocument:true so the reading-nav (Progress/tabs/TOC) renders — that's what
// the sidebar keeps after settings/account/admin move to the Settings page.
const baseSidebarProps = (over = {}) => ({
  theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true,
  sidebarTab: 'sentences', setSidebarTab: vi.fn(),
  hasDocument: true, pdfDoc: {}, pdfOutline: [], textItems: ['a'],
  currentSentenceIndex: 0, sentenceRefs: { current: [] },
  calculateReadingProgress: () => 42,
  handleMobileSentenceClick: vi.fn(), handleSentenceContextMenu: vi.fn(), handleChapterNavigation: vi.fn(),
  ...over,
});

describe('Sidebar — settings/account/admin moved to the Settings page', () => {
  it('no longer renders the Settings, Account, or Admin sections', () => {
    render(<Sidebar {...baseSidebarProps()} />);
    expect(screen.queryByRole('heading', { name: /^settings$/i })).toBeNull();
    expect(screen.queryByRole('heading', { name: /^account$/i })).toBeNull();
    expect(screen.queryByRole('heading', { name: /^admin$/i })).toBeNull();
  });

  it('still renders the reading navigation (progress)', () => {
    render(<Sidebar {...baseSidebarProps()} />);
    expect(screen.getByText(/progress/i)).toBeInTheDocument();
  });
});
