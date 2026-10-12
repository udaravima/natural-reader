import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import OldVersionBanner from './OldVersionBanner';

const old = { project: { id: 'p', name: 'Project X' }, document_id: 'x', version: 1, current_version: 3,
  current_doc_id: 'h3', current_name: 'report.pdf', is_current: false };

describe('OldVersionBanner', () => {
  it('names the version, the project and the current one, and opens it', () => {
    const onOpenCurrent = vi.fn();
    render(<OldVersionBanner old={old} onOpenCurrent={onOpenCurrent} />);
    expect(screen.getByRole('status').textContent).toContain('Old version — v1 of report.pdf in Project X (current is v3)');
    fireEvent.click(screen.getByRole('button', { name: 'Open current' }));
    expect(onOpenCurrent).toHaveBeenCalledWith(old);
  });
  it('renders nothing when the document is current', () => {
    const { container } = render(<OldVersionBanner old={null} onOpenCurrent={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('OldVersionBanner placement', () => {
  it('is in normal flow, not overlaid on the toolbar', () => {
    render(<OldVersionBanner old={old} onOpenCurrent={vi.fn()} />);
    const cls = screen.getByRole('status').className;
    expect(cls).not.toMatch(/\b(absolute|fixed)\b/);
    expect(screen.getByRole('button', { name: 'Open current' }).getAttribute('type')).toBe('button');
  });
});
