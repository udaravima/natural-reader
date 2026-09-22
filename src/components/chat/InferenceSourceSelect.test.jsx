import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { InferenceSourceSelect } from './InferenceSourceSelect';

const theme = { textSecondary: '', hover: '', border: '', bgTertiary: '' };

describe('InferenceSourceSelect', () => {
  it('marks the active source', () => {
    render(<InferenceSourceSelect source="server" onChange={vi.fn()} theme={theme} />);
    expect(screen.getByRole('button', { name: 'Server' }).className).toContain('bg-blue-500');
    expect(screen.getByRole('button', { name: 'Local Ollama' }).className).not.toContain('bg-blue-500');
  });

  it('marks local when selected', () => {
    render(<InferenceSourceSelect source="local" onChange={vi.fn()} theme={theme} />);
    expect(screen.getByRole('button', { name: 'Local Ollama' }).className).toContain('bg-blue-500');
  });

  it('fires onChange with the picked source', () => {
    const onChange = vi.fn();
    render(<InferenceSourceSelect source="server" onChange={onChange} theme={theme} />);
    fireEvent.click(screen.getByRole('button', { name: 'Local Ollama' }));
    expect(onChange).toHaveBeenCalledWith('local');
  });
});
