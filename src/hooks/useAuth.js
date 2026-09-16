import { useCallback, useEffect, useState } from 'react';
import { apiFetch, setUnauthorizedHandler } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';

/**
 * Auth state machine driven by the backend `/v1/auth/me` probe.
 *
 * state ∈ 'loading' | 'anonymous' | 'pending' | 'disabled' | 'active' | 'error'
 * user  = { id, email, role } | null   (only meaningful when active)
 */
export function useAuth(apiHost, apiPort) {
  const [state, setState] = useState('loading');
  const [user, setUser] = useState(null);

  const check = useCallback(async () => {
    setState('loading');
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/auth/me');
      if (res.ok) {
        setUser(await res.json());
        setState('active');
      } else if (res.status === 401) {
        setUser(null);
        setState('anonymous');
      } else if (res.status === 403) {
        const body = await res.json().catch(() => ({}));
        setState(body?.detail?.status === 'disabled' ? 'disabled' : 'pending');
      } else {
        setState('error');
      }
    } catch {
      setState('error');
    }
  }, [apiHost, apiPort]);

  // Any 401 from any call site drops us back to the login gate.
  useEffect(() => {
    setUnauthorizedHandler(() => { setUser(null); setState('anonymous'); });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => { check(); }, [check]);

  const login = useCallback(() => {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(buildApiUrl(apiHost, apiPort, `/v1/auth/login?next=${next}`));
  }, [apiHost, apiPort]);

  const logout = useCallback(async () => {
    try { await apiFetch(apiHost, apiPort, '/v1/auth/logout', { method: 'POST' }); }
    finally { setUser(null); setState('anonymous'); }
  }, [apiHost, apiPort]);

  return { state, user, login, logout, refresh: check };
}
