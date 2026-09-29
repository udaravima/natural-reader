import { useCallback, useEffect, useState } from 'react';
import { apiFetch, setUnauthorizedHandler, setForbiddenHandler } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';
import { setLibraryOwner } from '../db';

/**
 * One `/v1/auth/me` round trip → the auth state it means. Also tells the
 * local library whose it is (see setLibraryOwner): only a real signed-in id
 * claims pre-release records, and the 'local' fallback when /me can't be
 * reached claims nothing. Sets no React state — callers apply the result.
 */
async function readMe(apiHost, apiPort) {
  try {
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/me');
    if (res.ok) {
      const body = await res.json();
      // Await so the claim has finished before 'active' makes anything
      // refresh the library list for the now-known user.
      await setLibraryOwner(body.id, { claimLegacy: true });
      return { state: 'active', user: { ...body, capabilities: body.capabilities ?? [] } };
    }
    if (res.status === 401) {
      setLibraryOwner(null);
      return { state: 'anonymous', user: null };
    }
    if (res.status === 403) {
      const body = await res.json().catch(() => ({}));
      setLibraryOwner(null);
      return { state: body?.detail?.status === 'disabled' ? 'disabled' : 'pending', user: null };
    }
    // /v1/auth/me is erroring — no signed-in user is known, so the local
    // library falls back to the single shared "local" owner.
    setLibraryOwner('local');
    return { state: 'error', user: null };
  } catch {
    setLibraryOwner('local');
    return { state: 'error', user: null };
  }
}

/**
 * Auth state machine driven by the backend `/v1/auth/me` probe.
 *
 * state ∈ 'loading' | 'anonymous' | 'pending' | 'disabled' | 'active' | 'error'
 * user  = { id, email, role } | null   (only meaningful when active)
 */
export function useAuth(apiHost, apiPort) {
  const [state, setState] = useState('loading');
  const [user, setUser] = useState(null);

  const apply = useCallback((result) => {
    setUser(result.user);
    setState(result.state);
  }, []);

  // Re-probe from an event (Retry, a stale-capability 403): show the loading
  // gate again while it runs.
  const refresh = useCallback(() => {
    setState('loading');
    return readMe(apiHost, apiPort).then(apply);
  }, [apiHost, apiPort, apply]);

  // Any 401 from any call site drops us back to the login gate.
  useEffect(() => {
    setUnauthorizedHandler(() => { setUser(null); setState('anonymous'); setLibraryOwner(null); });
    return () => setUnauthorizedHandler(null);
  }, []);

  // A 403 missing_capability from any call site means our capability set is
  // stale (e.g. an admin revoked a capability mid-session) — re-probe /me
  // rather than booting to the login gate, since the session itself is fine.
  useEffect(() => {
    setForbiddenHandler(() => { refresh(); });
    return () => setForbiddenHandler(null);
  }, [refresh]);

  // The first probe: state already starts as 'loading'. The result is applied
  // in the promise's callback, and dropped if apiHost/apiPort changed meanwhile.
  useEffect(() => {
    let current = true;
    readMe(apiHost, apiPort).then((result) => { if (current) apply(result); });
    return () => { current = false; };
  }, [apiHost, apiPort, apply]);

  const login = useCallback(() => {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(buildApiUrl(apiHost, apiPort, `/v1/auth/login?next=${next}`));
  }, [apiHost, apiPort]);

  // Navigation, not fetch: the backend 303s to the IdP's end-session endpoint,
  // and the browser must follow that redirect itself so the IdP can clear its
  // SSO cookie on its own origin. The SPA state resets naturally on reload.
  const logout = useCallback(() => {
    window.location.assign(buildApiUrl(apiHost, apiPort, '/v1/auth/logout'));
  }, [apiHost, apiPort]);

  return { state, user, login, logout, refresh };
}
