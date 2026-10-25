import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ConsoleShell } from './ConsoleShell';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const sections = [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }, { id: 'c', label: 'C' }];

function shell(over = {}) {
  const props = { theme, sections, active: 'a', onSelect: vi.fn(), onBack: vi.fn(), dirty: false, ...over };
  render(<ConsoleShell {...props}><p>panel body</p></ConsoleShell>);
  return props;
}

describe('ConsoleShell', () => {
  it('has a tab per section and a phone picker with the same sections', () => {
    shell();
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent);
    const picker = screen.getByLabelText('Admin section');
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(tabs);
    expect(tabs).toEqual(['A', 'B', 'C']);
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'admin-tab-a');
    expect(screen.getByRole('tab', { name: 'A' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: 'B' })).toHaveAttribute('tabindex', '-1');
  });

  it('moves with the arrow keys, Home and End', () => {
    const { onSelect } = shell();
    const list = screen.getByRole('tablist');
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenLastCalledWith('b');
    fireEvent.keyDown(list, { key: 'End' });
    expect(onSelect).toHaveBeenLastCalledWith('c');
    fireEvent.keyDown(list, { key: 'ArrowUp' });
    expect(onSelect).toHaveBeenLastCalledWith('c');   // wraps from the first
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'C' }));
  });

  it('wraps forwards from the last section and Home goes to the first', () => {
    const { onSelect } = shell({ active: 'c' });
    const list = screen.getByRole('tablist');
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenLastCalledWith('a');
    fireEvent.keyDown(list, { key: 'Home' });
    expect(onSelect).toHaveBeenLastCalledWith('a');
  });

  it('asks before leaving a section with unsaved changes', () => {
    const { onSelect } = shell({ dirty: true });
    fireEvent.click(screen.getByRole('tab', { name: 'B' }));
    expect(onSelect).not.toHaveBeenCalled();
    const alert = screen.getByRole('alertdialog');
    expect(alert).toHaveTextContent('You have unsaved changes in A.');
    expect(alert).toHaveAttribute('aria-modal', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'A' }));
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: 'B' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(onSelect).toHaveBeenCalledWith('b');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'B' }));
  });

  it('asks before the phone picker leaves a dirty section', () => {
    const { onSelect } = shell({ dirty: true });
    fireEvent.change(screen.getByLabelText('Admin section'), { target: { value: 'c' } });
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });

  it('asks before going back to the reader, and Escape keeps editing', () => {
    const { onBack } = shell({ dirty: true });
    fireEvent.click(screen.getByRole('button', { name: /Back to reader/ }));
    expect(onBack).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toHaveTextContent('You have unsaved changes in A.');

    fireEvent.keyDown(document.activeElement, { key: 'Escape' });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(onBack).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Back to reader/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('goes straight back when nothing is unsaved', () => {
    const { onBack } = shell();
    fireEvent.click(screen.getByRole('button', { name: /Back to reader/ }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  describe('on a phone', () => {
    afterEach(() => vi.unstubAllGlobals());

    it('returns focus to the picker after Keep editing and after Discard', () => {
      vi.stubGlobal('matchMedia', () => ({ matches: false }));
      shell({ dirty: true });
      const picker = screen.getByLabelText('Admin section');
      fireEvent.change(picker, { target: { value: 'b' } });
      fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
      expect(document.activeElement).toBe(picker);

      screen.getByRole('button', { name: /Back to reader/ }).focus();
      fireEvent.change(picker, { target: { value: 'c' } });
      fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
      expect(document.activeElement).toBe(picker);
    });
  });
});
