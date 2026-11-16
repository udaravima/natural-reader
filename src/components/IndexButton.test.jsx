import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import IndexButton from './IndexButton';

const theme = {
  bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '',
  textSecondary: '', textMuted: '', hover: '',
};

describe('IndexButton', () => {
  it('extracting renders a disabled button labeled "Extracting"', () => {
    render(<IndexButton theme={theme} state="extracting" onIndex={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /Extracting/ });
    expect(btn).toBeDisabled();
  });

  it.each(['stored', 'extracted'])('%s renders an enabled "Resume" button that calls onIndex', async (state) => {
    const onIndex = vi.fn();
    render(<IndexButton theme={theme} state={state} onIndex={onIndex} />);
    const btn = screen.getByRole('button', { name: /Resume/ });
    expect(btn).not.toBeDisabled();
    await userEvent.click(btn);
    expect(onIndex).toHaveBeenCalledTimes(1);
  });

  it.each(['stored', 'extracted'])('%s with a job in the queue waits, saying how many are ahead, and offers no Resume', (state) => {
    render(<IndexButton theme={theme} state={state} queued queueAhead={2} onIndex={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /Waiting to index \(2 ahead\)/ });
    expect(btn).toBeDisabled();
    expect(btn.title).not.toMatch(/interrupted/i);
    expect(screen.queryByRole('button', { name: /Resume/ })).toBeNull();
  });

  it('a queued document next in line just waits', () => {
    render(<IndexButton theme={theme} state="stored" queued queueAhead={0} onIndex={vi.fn()} />);
    expect(screen.getByRole('button', { name: /^Waiting to index$/ })).toBeDisabled();
  });

  it('indexing shows the embedded/chunk progress', () => {
    render(<IndexButton theme={theme} state="indexing" embeddedCount={3} chunkCount={9} onIndex={vi.fn()} />);
    expect(screen.getByText(/3\/9/)).toBeInTheDocument();
  });

  it('indexed renders an enabled "Indexed" button', () => {
    render(<IndexButton theme={theme} state="indexed" chunkCount={5} onIndex={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /Indexed/ });
    expect(btn).not.toBeDisabled();
  });

  it('failed renders an enabled "Retry index" button', () => {
    render(<IndexButton theme={theme} state="failed" onIndex={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /Retry index/ });
    expect(btn).not.toBeDisabled();
  });

  it('idle renders an enabled "Index" button', () => {
    render(<IndexButton theme={theme} state="idle" onIndex={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /^Index$/ });
    expect(btn).not.toBeDisabled();
  });
});
