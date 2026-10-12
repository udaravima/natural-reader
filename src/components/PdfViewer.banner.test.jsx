import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import PdfViewer from './PdfViewer';
import OldVersionBanner from './OldVersionBanner';

const theme = { bg: '', bgSecondary: 'bg2', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '', viewportBg: '' };
const old = { project: { id: 'p', name: 'Project X' }, document_id: 'x', version: 1, current_version: 3,
  current_doc_id: 'h3', current_name: 'report.pdf', is_current: false };
const props = (over = {}) => ({
  theme, darkMode: false, effectiveIsMobile: false, pdfDoc: null, fileType: 'text', textItems: [],
  currentSentenceIndex: -1, currentPage: 1, setCurrentPage: vi.fn(), goToNextPage: vi.fn(), goToPrevPage: vi.fn(),
  numPages: 2, scale: 1, setScale: vi.fn(), recentBooks: [], markdownPageData: [], ...over,
});
const banner = <OldVersionBanner old={old} onOpenCurrent={vi.fn()} />;

describe('PdfViewer banner slot', () => {
  it('renders the banner before, and outside, the toolbar, leaving the toolbar controls reachable', () => {
    render(<PdfViewer {...props({ banner })} />);
    const status = screen.getByRole('status');
    const zoomOut = screen.getByTitle('Zoom Out (Ctrl+-)');
    expect(status.contains(zoomOut)).toBe(false);
    expect(zoomOut.closest('div.border-b')?.contains(status)).toBeFalsy();
    expect(status.compareDocumentPosition(zoomOut) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  it('still shows the banner in distraction-free mode', () => {
    render(<PdfViewer {...props({ banner, distractionFree: true })} />);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.queryByTitle('Zoom Out (Ctrl+-)')).toBeNull();
  });
});
