import { useEffect } from 'react';

/**
 * Boot coercion for the admin view: a persisted viewMode 'admin' is only
 * valid while the current user is actually an admin. Waits for the auth
 * probe to resolve ('active') so a freshly-loading admin isn't bounced to
 * reader before /v1/auth/me has answered. Everything else (pending,
 * disabled, anonymous, error) renders the AuthGate, so 'active' is the only
 * state where a decision is meaningful.
 */
export function useViewModeGuard({ viewMode, setViewMode, authState, role }) {
  useEffect(() => {
    if (viewMode === 'admin' && authState === 'active' && role !== 'admin') {
      setViewMode('reader');
    }
  }, [viewMode, setViewMode, authState, role]);
}
