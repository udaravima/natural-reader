import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SpeakingSwitch } from './SpeakingSwitch';

const theme = { bgTertiary: '', bgSecondary: '', border: '', textSecondary: '', textMuted: '', text: '', hover: '' };
const OPTIONS = [
  { code: 'auto', label: 'Auto', detail: 'Whisper small' },
  { code: 'en', label: 'English', detail: 'Whisper small' },
  { code: 'si', label: 'Sinhala', detail: 'Whisper small, Sinhala (run12)' },
];

describe('SpeakingSwitch', () => {
  it('is one button that says what you are speaking, "Auto (English)" where Auto means English', () => {
    render(<SpeakingSwitch theme={theme} value="auto" options={OPTIONS} onChange={vi.fn()} autoMeans="English" />);
    const button = screen.getByRole('button', { name: 'Speaking: Auto (English)' });
    expect(button).toHaveAttribute('aria-haspopup', 'menu');
    expect(button).toHaveTextContent('Auto (English)');
  });

  it('a tap on a language switches to it and closes', () => {
    const onChange = vi.fn();
    render(<SpeakingSwitch theme={theme} value="en" options={OPTIONS} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Speaking: English' }));
    expect(screen.getByRole('menuitemradio', { name: /English/ })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Sinhala/ }));
    expect(onChange).toHaveBeenCalledWith('si');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('All languages opens Settings, for the ones not listed here', () => {
    const onMore = vi.fn();
    render(<SpeakingSwitch theme={theme} value="en" options={OPTIONS} onChange={vi.fn()} onMore={onMore} />);
    fireEvent.click(screen.getByRole('button', { name: 'Speaking: English' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'All languages…' }));
    expect(onMore).toHaveBeenCalledTimes(1);
  });

  it('with only Auto to offer, it isn\'t shown at all', () => {
    const { container } = render(<SpeakingSwitch theme={theme} value="auto" options={[OPTIONS[0]]} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
