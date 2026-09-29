import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch, setUnauthorizedHandler, setForbiddenHandler } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';
import { setLibraryOwner } from '../db';

/**
 * One `/v1/auth/me` round trip → the auth state it means. Also tells the
 * local library whose it is (see setLibraryOwner): only a real signed-in id
 * claims pre-release records; any other outcome leaves no owner, so nothing
 * can be read or saved locally. Sets no React state — callers apply the result.
 * `isCurrent()` says whether this probe is still the latest: a superseded
 * one (the host changed, or a refresh started) leaves the owner alone.
 */
async function readMe(apiHost, apiPort, isCurrent = () => true) {
  const setOwner = (...args) => (isCurrent() ? setLibraryOwner(...args) : undefined);
  try {
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/me');
    if (res.ok) {
      const body = await res.json();
      // Await so the claim has finished before 'active' makes anything
      // refresh the library list for the now-known user.
      await setOwner(body.id, { claimLegacy: true });
      return { state: 'active', user: { ...body, capabilities: body.capabilities ?? [] } };
    }
    if (res.status === 401) {
      setOwner(null);
      return { state: 'anonymous', user: null };
    }
    if (res.status === 403) {
      const body = await res.json().catch(() => ({}));
      setOwner(null);
      return { state: body?.detail?.status === 'disabled' ? 'disabled' : 'pending', user: null };
    }
    // /v1/auth/me is erroring — no signed-in user is known. No owner, not a
    // shared one: a bucket everyone on this browser shares is exactly what
    // the per-user library exists to prevent (final review, ruling R2 revised).
    setOwner(null);
    return { state: 'error', user: null };
  } catch {
    setOwner(null);
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

  // Only the latest probe counts: each one takes a number, and a result (and
  // its library-owner change) is dropped once a newer probe has started.
  const probeSeq = useRef(0);
  const probe = useCallback(() => {
    const seq = ++probeSeq.current;
    const isCurrent = () => seq === probeSeq.current;
    return readMe(apiHost, apiPort, isCurrent).then((result) => {
      if (!isCurrent()) return;
      setUser(result.user);
      setState(result.state);
    });
  }, [apiHost, apiPort]);

  // Re-probe from an event (Retry, a stale-capability 403): show the loading
  // gate again while it runs.
  const refresh = useCallback(() => {
    setState('loading');
    return probe();
  }, [probe]);

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

  // The first probe, and a new one when apiHost/apiPort change. State starts
  // as 'loading'; on a host change the previous state stays until the new
  // result lands (no loading flash under Settings).
  useEffect(() => {
    probe();
  }, [probe]);

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
