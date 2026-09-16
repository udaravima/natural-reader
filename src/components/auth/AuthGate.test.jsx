import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AuthGate } from './AuthGate';

const child = <div data-testid="app">APP</div>;

describe('AuthGate', () => {
  it('renders children only when active', () => {
    render(<AuthGate state="active">{child}</AuthGate>);
    expect(screen.getByTestId('app')).toBeInTheDocument();
  });

  it('shows the login screen and fires onLogin when anonymous', () => {
    const onLogin = vi.fn();
    render(<AuthGate state="anonymous" onLogin={onLogin}>{child}</AuthGate>);
    expect(screen.queryByTestId('app')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(onLogin).toHaveBeenCalledOnce();
  });

  it('shows the awaiting-approval screen when pending', () => {
    render(<AuthGate state="pending" onLogout={vi.fn()}>{child}</AuthGate>);
    expect(screen.getByText(/awaiting approval/i)).toBeInTheDocument();
    expect(screen.queryByTestId('app')).toBeNull();
  });

  it('shows the disabled screen when disabled', () => {
    render(<AuthGate state="disabled" onLogout={vi.fn()}>{child}</AuthGate>);
    expect(screen.getByText(/disabled/i)).toBeInTheDocument();
  });

  it('shows a retry on error', () => {
    const onRetry = vi.fn();
    render(<AuthGate state="error" onRetry={onRetry}>{child}</AuthGate>);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
