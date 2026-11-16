import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PdfViewer from './PdfViewer';

vi.mock('./MarkdownReader', () => ({ default: () => <div data-testid="md-reader" /> }));

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '', viewportBg: '', canvasBg: '' };
const props = (over = {}) => ({
  theme, darkMode: false, effectiveIsMobile: false, pdfDoc: {}, fileType: 'pdf', textItems: [],
  currentSentenceIndex: -1, currentPage: 1, setCurrentPage: vi.fn(), goToNextPage: vi.fn(), goToPrevPage: vi.fn(),
  numPages: 2, scale: 1, setScale: vi.fn(), recentBooks: [], markdownPageData: [],
  convertState: 'converted', viewMode: 'both', setViewMode: vi.fn(), docId: 'd1', ...over,
});

describe('PdfViewer side by side', () => {
  it('shows the PDF page and the Markdown together, each in its own scrolling column', () => {
    const { container } = render(<PdfViewer {...props()} />);
    const canvas = container.querySelector('canvas');
    const md = screen.getByTestId('md-reader');
    expect(canvas).toBeTruthy();
    const pdfPane = canvas.closest('[data-pane="pdf"]');
    const mdPane = md.closest('[data-pane="md"]');
    expect(pdfPane).toBeTruthy();
    expect(mdPane).toBeTruthy();
    expect(pdfPane).not.toBe(mdPane);
    expect(pdfPane.className).toContain('overflow-auto');
    expect(mdPane.className).toContain('overflow-auto');
  });

  it('offers Both between PDF and MD, lit when chosen', () => {
    const setViewMode = vi.fn();
    render(<PdfViewer {...props({ viewMode: 'pdf', setViewMode })} />);
    const both = screen.getByTitle('Show the PDF and the Markdown side by side');
    expect(both.className).not.toContain('bg-blue-600');
    fireEvent.click(both);
    expect(setViewMode).toHaveBeenCalledWith('both');
  });

  it('lights only Both when both are shown', () => {
    render(<PdfViewer {...props()} />);
    expect(screen.getByTitle('Show the PDF and the Markdown side by side').className).toContain('bg-blue-600');
    expect(screen.getByTitle('Show the original PDF rendering').className).not.toContain('bg-blue-600');
  });

  it('shows only the PDF on a phone, and does not offer Both there', () => {
    const { container } = render(<PdfViewer {...props({ effectiveIsMobile: true })} />);
    expect(container.querySelector('canvas')).toBeTruthy();
    expect(screen.queryByTestId('md-reader')).toBeNull();
    expect(screen.queryByTitle('Show the PDF and the Markdown side by side')).toBeNull();
  });

  it('shows only the PDF when the document is not converted', () => {
    const { container } = render(<PdfViewer {...props({ convertState: 'idle' })} />);
    expect(container.querySelector('canvas')).toBeTruthy();
    expect(screen.queryByTestId('md-reader')).toBeNull();
  });
});
