import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { InferenceRow } from './InferenceRow';

const theme = { border: '', bgSecondary: '', text: '', textSecondary: '', textMuted: '' };

describe('InferenceRow', () => {
  it('renders the label and options and calls onChange with the picked value', () => {
    const onChange = vi.fn();
    render(<InferenceRow theme={theme} label="Context window" value="auto"
      onChange={onChange} options={[['auto', 'Auto'], ['4096', '4096']]} />);
    expect(screen.getByText('Context window')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '4096' } });
    expect(onChange).toHaveBeenCalledWith('4096');
  });

  it('lets the select grow to its longest option instead of clipping it at a fixed width', () => {
    render(<InferenceRow theme={theme} label="Context window" value="auto" onChange={() => {}}
      options={[['auto', 'Auto (16,384 on this server)'], ['4096', '4096']]} />);
    const select = screen.getByRole('combobox');
    expect(select.options[0].textContent).toBe('Auto (16,384 on this server)');
    // A fixed w-32 (128 px) clipped this label to "Auto (16,384 on thi"; a floor keeps the short rows aligned,
    // and max-w-full keeps a long one inside the dialog (the label truncates instead)
    expect(select.className.split(/\s+/)).not.toContain('w-32');
    expect(select.className.split(/\s+/)).toEqual(expect.arrayContaining(['min-w-32', 'max-w-full']));
  });
});
