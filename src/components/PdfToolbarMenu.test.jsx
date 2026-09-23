import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PdfToolbarMenu from './PdfToolbarMenu';

const theme = {
  border: '', bgSecondary: '', bgTertiary: '', hover: '',
  text: '', textSecondary: '', textMuted: '',
};
const Dummy = () => null; // icon stand-in

describe('PdfToolbarMenu', () => {
  it('renders nothing when there are no actions', () => {
    const { container } = render(<PdfToolbarMenu theme={theme} actions={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('opens on click, fires the chosen action, and closes afterward', () => {
    const onFit = vi.fn();
    render(
      <PdfToolbarMenu
        theme={theme}
        actions={[{ key: 'fit', label: 'Fit Page', Icon: Dummy, onClick: onFit }]}
      />
    );
    // Closed initially — the item isn't in the DOM.
    expect(screen.queryByRole('menuitem', { name: /fit page/i })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /more/i }));
    const item = screen.getByRole('menuitem', { name: /fit page/i });
    fireEvent.click(item);

    expect(onFit).toHaveBeenCalledTimes(1);
    // Selecting an item closes the menu.
    expect(screen.queryByRole('menuitem', { name: /fit page/i })).toBeNull();
  });

  it('skips falsy action entries (conditional items)', () => {
    render(
      <PdfToolbarMenu
        theme={theme}
        actions={[
          { key: 'a', label: 'Kept', Icon: Dummy, onClick: vi.fn() },
          false,
          null,
        ]}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: /more/i }));
    expect(screen.getByRole('menuitem', { name: /kept/i })).toBeInTheDocument();
    expect(screen.getAllByRole('menuitem')).toHaveLength(1);
  });

  it('does not fire a disabled action', () => {
    const onClick = vi.fn();
    render(
      <PdfToolbarMenu
        theme={theme}
        actions={[{ key: 'x', label: 'Nope', Icon: Dummy, onClick, disabled: true }]}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: /more/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /nope/i }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
