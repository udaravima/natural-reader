import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AuthGate } from './AuthGate';

const child = <div data-testid="app">APP</div>;

describe('AuthGate', () => {
  it('renders children only when active with capabilities', () => {
    render(<AuthGate state="active" user={{ capabilities: ['reader'] }}>{child}</AuthGate>);
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

  it('shows access-not-granted when active but no capabilities', () => {
    render(
      <AuthGate state="active" user={{ capabilities: [] }}
                onLogin={() => {}} onLogout={() => {}} onRetry={() => {}}>
        <div>APP</div>
      </AuthGate>);
    expect(screen.queryByText('APP')).toBeNull();
    expect(screen.getByRole('heading', { name: /active but has no access/i })).toBeInTheDocument();
  });

  // `talk` and `docling` only add to Reader or Chat: alone they open nothing, and the app would sit on Loading….
  it.each([[['talk']], [['docling']], [['docling', 'talk']]])('shows access-not-granted for %j alone', (caps) => {
    render(
      <AuthGate state="active" user={{ capabilities: caps }}
                onLogin={() => {}} onLogout={() => {}} onRetry={() => {}}>
        <div>APP</div>
      </AuthGate>);
    expect(screen.queryByText('APP')).toBeNull();
    expect(screen.getByRole('heading', { name: /active but has no access/i })).toBeInTheDocument();
  });

  it('renders children when active with a capability', () => {
    render(
      <AuthGate state="active" user={{ capabilities: ['reader'] }}
                onLogin={() => {}} onLogout={() => {}} onRetry={() => {}}>
        <div>APP</div>
      </AuthGate>);
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it("shows the deployment's name, tagline, version and copyright on the sign-in screen", () => {
    render(<AuthGate state="anonymous" onLogin={() => {}} onLogout={() => {}} onRetry={() => {}}
                     identity={{ name: 'Acme Docs', tagline: 'a library for contracts', copyright: '© 2026 Example Org' }} />);
    expect(screen.getByRole('heading', { name: 'Acme Docs' })).toBeInTheDocument();
    expect(screen.getByText('A library for contracts')).toBeInTheDocument();
    expect(screen.getByText(/^Acme Docs \d+\.\d+\.\d+ · © 2026 Example Org$/)).toBeInTheDocument();
  });

  it('keeps the built-in name when no identity is given', () => {
    render(<AuthGate state="anonymous" onLogin={() => {}} onLogout={() => {}} onRetry={() => {}} />);
    expect(screen.getByRole('heading', { name: 'Natural Reader' })).toBeInTheDocument();
  });
});
