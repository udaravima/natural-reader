import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ConvertButton from './ConvertButton';

const theme = {
  bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};

const converting = (progress) =>
  render(<ConvertButton theme={theme} state="converting" progress={progress} onClick={vi.fn()} />);

describe('ConvertButton while Docling converts', () => {
  it('counts the pages done, from the server\'s conversion_progress', () => {
    converting({ phase: 'build', done: 3, total: 40 });
    const btn = screen.getByRole('button', { name: /Converting 3\/40/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', expect.stringMatching(/3 of 40 pages/));
  });

  it('names the phases that have no page count', () => {
    converting({ phase: 'initialize' });
    expect(screen.getByRole('button', { name: /Preparing/ })).toBeInTheDocument();
  });

  it('shows enrichment items, with the step in the tooltip', () => {
    converting({ phase: 'enrich', done: 2, total: 5, label: 'picture description' });
    const btn = screen.getByRole('button', { name: /Enriching 2\/5/ });
    expect(btn).toHaveAttribute('title', expect.stringMatching(/picture description/));
  });

  it('no report yet (or an older server): just "Converting"', () => {
    converting(null);
    expect(screen.getByRole('button', { name: /^Converting$/ })).toBeInTheDocument();
  });
});

describe('ConvertButton after a failed conversion', () => {
  it('the server\'s conversion_failed offers a retry with the reason, like a failed upload', () => {
    render(<ConvertButton theme={theme} state="conversion_failed" error="bad pdf" onClick={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /Retry convert/ });
    expect(btn).toHaveAttribute('title', 'Conversion failed: bad pdf');
  });
});
