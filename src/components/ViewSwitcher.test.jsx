import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ViewSwitcher from './ViewSwitcher';

const theme = { bgTertiary: '', border: '', textSecondary: '' };

describe('ViewSwitcher', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the admin shield for an admin', () => {
    render(<ViewSwitcher theme={theme} viewMode="reader" setViewMode={vi.fn()} isAdmin />);
    expect(screen.getByRole('button', { name: /admin/i })).toBeInTheDocument();
  });

  it('never renders the admin shield for a member', () => {
    render(<ViewSwitcher theme={theme} viewMode="reader" setViewMode={vi.fn()} isAdmin={false} />);
    expect(screen.queryByRole('button', { name: /admin/i })).toBeNull();
  });

  it('switches to the admin view on click', () => {
    const setViewMode = vi.fn();
    render(<ViewSwitcher theme={theme} viewMode="reader" setViewMode={setViewMode} isAdmin />);
    fireEvent.click(screen.getByRole('button', { name: /admin/i }));
    expect(setViewMode).toHaveBeenCalledWith('admin');
  });

  it('renders the library button for every user, admin or not', () => {
    render(<ViewSwitcher theme={theme} viewMode="reader" setViewMode={vi.fn()} isAdmin={false} />);
    expect(screen.getByRole('button', { name: /library/i })).toBeInTheDocument();
  });

  it('switches to the library view on click', () => {
    const setViewMode = vi.fn();
    render(<ViewSwitcher theme={theme} viewMode="reader" setViewMode={setViewMode} isAdmin={false} />);
    fireEvent.click(screen.getByRole('button', { name: /library/i }));
    expect(setViewMode).toHaveBeenCalledWith('library');
  });

  it('does not light up the reader button while in library mode', () => {
    render(<ViewSwitcher theme={theme} viewMode="library" setViewMode={vi.fn()} isAdmin={false} />);
    const readerBtn = screen.getByRole('button', { name: /reader/i });
    expect(readerBtn.className).not.toMatch(/bg-blue-600/);
    const libraryBtn = screen.getByRole('button', { name: /library/i });
    expect(libraryBtn.className).toMatch(/bg-blue-600/);
  });
});
