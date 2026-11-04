import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ViewMenu } from './ViewSwitcher';

const theme = { bgTertiary: '', bgSecondary: '', border: '', textSecondary: '', text: '', hover: '' };

// On a phone the four view buttons don't fit beside Settings, the profile and the reader's own buttons: an admin's
// header was 370 px on a 360 px screen, so the page widened to 422 px and Settings was cut off on the right. One
// button for the view, with the others in a menu.
describe('ViewMenu, the view switch on a phone', () => {
  it('is one button that says which view is open', () => {
    render(<ViewMenu theme={theme} viewMode="chat" setViewMode={vi.fn()} isAdmin />);
    const button = screen.getByRole('button', { name: 'View: Chat' });
    expect(button).toHaveAttribute('aria-haspopup', 'menu');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('lists the views this person may use, the open one checked', () => {
    render(<ViewMenu theme={theme} viewMode="library" setViewMode={vi.fn()} isAdmin />);
    fireEvent.click(screen.getByRole('button', { name: 'View: Library' }));
    const items = screen.getAllByRole('menuitemradio');
    expect(items.map((i) => i.textContent)).toEqual(['Reader', 'Chat', 'Library', 'Admin']);
    expect(screen.getByRole('menuitemradio', { name: 'Library' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('menuitemradio', { name: 'Reader' })).toHaveAttribute('aria-checked', 'false');
  });

  it('leaves out what the person can\'t open: Admin for a member, Reader without the reader role', () => {
    render(<ViewMenu theme={theme} viewMode="chat" setViewMode={vi.fn()} isAdmin={false} canReader={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'View: Chat' }));
    expect(screen.getAllByRole('menuitemradio').map((i) => i.textContent)).toEqual(['Chat', 'Library']);
  });

  it('choosing a view switches to it and closes the menu', () => {
    const setViewMode = vi.fn();
    render(<ViewMenu theme={theme} viewMode="reader" setViewMode={setViewMode} isAdmin />);
    fireEvent.click(screen.getByRole('button', { name: 'View: Reader' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Admin' }));
    expect(setViewMode).toHaveBeenCalledWith('admin');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('Escape closes it', () => {
    render(<ViewMenu theme={theme} viewMode="reader" setViewMode={vi.fn()} isAdmin />);
    fireEvent.click(screen.getByRole('button', { name: 'View: Reader' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
