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
});
