import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect, useState } from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { TalkSettingsPanel } from './TalkSettingsPanel';

const theme = { border: '', bgTertiary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const WINDOW = { vad_start: 0.5, vad_stay: 0.35, min_speech_ms: 250, silence_ms: 700, gate: false, gate_db: 9 };

const record = vi.fn();
const stored = () => record.mock.calls.at(-1)[0];
function Harness({ initial = {}, defaults = WINDOW }) {
  const [choices, setChoices] = useState(initial);
  useEffect(() => { record(choices); }, [choices]);
  return <TalkSettingsPanel theme={theme} t={{ defaults, choices, setChoices }} />;
}
beforeEach(() => { record.mockClear(); });
const row = (label) => screen.getByRole('group', { name: label });

describe('TalkSettingsPanel', () => {
  it('shows every default, the known-good values, beside each control', () => {
    render(<Harness />);
    expect(within(row('Pause that ends your turn')).getByText('Default: 700 ms')).toBeInTheDocument();
    expect(within(row('Automatic gain')).getByText('Default: on')).toBeInTheDocument();
    expect(within(row('Ignore quieter voices')).getByText('Default: off')).toBeInTheDocument();
  });

  it('the defaults are the server\'s: an admin\'s value shows as the default', () => {
    render(<Harness defaults={{ ...WINDOW, silence_ms: 900 }} />);
    expect(within(row('Pause that ends your turn')).getByText('Default: 900 ms')).toBeInTheDocument();
  });

  it('switches the gate on, and only then offers how much quieter', () => {
    render(<Harness />);
    expect(screen.queryByRole('group', { name: 'How much quieter' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Ignore quieter voices' }));
    expect(stored()).toEqual({ gate: true });
    expect(screen.getByRole('switch', { name: 'Ignore quieter voices' })).toHaveAttribute('aria-checked', 'true');
    expect(within(row('How much quieter')).getByText('Default: 9 dB')).toBeInTheDocument();
  });

  it('a slider stores the number, and shows it beside the default', () => {
    render(<Harness />);
    fireEvent.change(screen.getByRole('slider', { name: 'Pause that ends your turn' }), { target: { value: '1200' } });
    expect(stored()).toEqual({ silence_ms: 1200 });
    expect(within(row('Pause that ends your turn')).getByText('1200 ms')).toBeInTheDocument();
  });

  // Back at the default, the choice is forgotten: it follows the server again if an admin changes it.
  it('moving a control back to its default forgets the choice', () => {
    render(<Harness initial={{ silence_ms: 1200, auto_gain: false }} />);
    fireEvent.change(screen.getByRole('slider', { name: 'Pause that ends your turn' }), { target: { value: '700' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Automatic gain' }));
    expect(stored()).toEqual({});
  });

  it('Reset to defaults forgets every choice, and is off when there is none', () => {
    render(<Harness initial={{ gate: true, gate_db: 6, silence_ms: 1200 }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    expect(stored()).toEqual({});
    expect(screen.getByRole('button', { name: 'Reset to defaults' })).toBeDisabled();
  });

  it('before the server has answered, it shows the measured values as defaults', () => {
    render(<Harness defaults={null} />);
    expect(within(row('Pause that ends your turn')).getByText('Default: 700 ms')).toBeInTheDocument();
  });
});
