import { useCallback, useEffect, useState } from 'react';
import { apiFetch, setUnauthorizedHandler, setForbiddenHandler } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';
import { setLibraryOwner } from '../db';

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
        const body = await res.json();
        // Await so the local library's per-user claim (see setLibraryOwner)
        // has finished before `state` flips to 'active' and anything reacts
        // to the now-known user id by refreshing the library list. Only a
        // real /me id claims pre-release records — never the 'local' fallback.
        await setLibraryOwner(body.id, { claimLegacy: true });
        setUser({ ...body, capabilities: body.capabilities ?? [] });
        setState('active');
      } else if (res.status === 401) {
        setUser(null);
        setState('anonymous');
        setLibraryOwner(null);
      } else if (res.status === 403) {
        const body = await res.json().catch(() => ({}));
        setState(body?.detail?.status === 'disabled' ? 'disabled' : 'pending');
        setLibraryOwner(null);
      } else {
        setState('error');
        // /v1/auth/me is unreachable/erroring — no signed-in user is known,
        // so the local library falls back to the single shared "local" owner
        // rather than hiding everything. It claims nothing (see above).
        setLibraryOwner('local');
      }
    } catch {
      setState('error');
      setLibraryOwner('local');
    }
  }, [apiHost, apiPort]);

  // Any 401 from any call site drops us back to the login gate.
  useEffect(() => {
    setUnauthorizedHandler(() => { setUser(null); setState('anonymous'); setLibraryOwner(null); });
    return () => setUnauthorizedHandler(null);
  }, []);

  // A 403 missing_capability from any call site means our capability set is
  // stale (e.g. an admin revoked a capability mid-session) — re-probe /me
  // rather than booting to the login gate, since the session itself is fine.
  useEffect(() => {
    setForbiddenHandler(() => { check(); });
    return () => setForbiddenHandler(null);
  }, [check]);

  useEffect(() => { check(); }, [check]);

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

  return { state, user, login, logout, refresh: check };
}
