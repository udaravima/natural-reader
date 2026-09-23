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
});
