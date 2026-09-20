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

  it('hides the chat switch for a reader-only user (canChat=false)', () => {
    render(<ViewSwitcher theme={theme} viewMode="reader" setViewMode={vi.fn()} isAdmin={false} canReader canChat={false} />);
    expect(screen.getByRole('button', { name: /reader/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /chat/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /admin/i })).toBeNull();
  });

  it('hides the reader switch for a chat-only user (canReader=false)', () => {
    render(<ViewSwitcher theme={theme} viewMode="chat" setViewMode={vi.fn()} isAdmin={false} canReader={false} canChat />);
    expect(screen.queryByRole('button', { name: /reader/i })).toBeNull();
    expect(screen.getByRole('button', { name: /chat/i })).toBeInTheDocument();
  });

  it('shows only the admin entry for an admin-only user (no reader, no chat)', () => {
    render(<ViewSwitcher theme={theme} viewMode="admin" setViewMode={vi.fn()} isAdmin canReader={false} canChat={false} />);
    expect(screen.queryByRole('button', { name: /reader/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /chat/i })).toBeNull();
    expect(screen.getByRole('button', { name: /admin/i })).toBeInTheDocument();
  });
});
