import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProfileMenu from './ProfileMenu';

const theme = { border: '', bgSecondary: '', bgTertiary: '', hover: '', text: '', textSecondary: '', textMuted: '' };
const base = (over = {}) => ({
  theme, user: { email: 'me@x.io', display_name: 'Me' },
  darkMode: false, setDarkMode: vi.fn(), setViewMode: vi.fn(), onLogout: vi.fn(), ...over,
});

describe('ProfileMenu', () => {
  it('opens and shows the identity', () => {
    render(<ProfileMenu {...base()} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    expect(screen.getByText('me@x.io')).toBeInTheDocument();
  });
  it('Settings opens the settings view', () => {
    const setViewMode = vi.fn();
    render(<ProfileMenu {...base({ setViewMode })} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /settings/i }));
    expect(setViewMode).toHaveBeenCalledWith('settings');
  });
  it('Log out calls onLogout', () => {
    const onLogout = vi.fn();
    render(<ProfileMenu {...base({ onLogout })} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /log out/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
  it('dark-mode item toggles the theme', () => {
    const setDarkMode = vi.fn();
    render(<ProfileMenu {...base({ setDarkMode })} />);
    fireEvent.click(screen.getByRole('button', { name: /account|profile/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /dark mode|light mode/i }));
    expect(setDarkMode).toHaveBeenCalled();
  });
});
