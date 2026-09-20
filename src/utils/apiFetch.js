import { buildApiUrl } from './url';

// A single process-wide handler so any 401 — from any call site — can drop the
// app back to the login gate without threading a callback through every caller.
let _onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  _onUnauthorized = fn;
}

// Parallel seam for 403s that specifically mean "your token is valid but you
// lack a capability the server just started requiring" (e.g. an admin was
// revoked mid-session). Distinct from a plain 401 — the user is still
// authenticated, so we re-probe /v1/auth/me rather than booting them to the
// login gate.
let _onForbidden = null;

export function setForbiddenHandler(fn) {
  _onForbidden = fn;
}

/**
 * Fetch a backend `/v1` endpoint with the session cookie attached.
 *
 * @param {string} host  apiHost setting ('' = same origin, behind the proxy)
 * @param {string} port  apiPort setting (ignored when host is blank)
 * @param {string} path  e.g. '/v1/auth/me'
 * @param {RequestInit} opts  merged over the defaults
 * @returns {Promise<Response>}
 */
export async function apiFetch(host, port, path, opts = {}) {
  const res = await fetch(buildApiUrl(host, port, path), {
    credentials: 'include',
    ...opts,
  });
  if (res.status === 401 && _onUnauthorized) _onUnauthorized();
  if (res.status === 403 && _onForbidden) {
    // Best-effort: clone so the caller can still read the original body, and
    // never let a malformed/empty 403 body throw out of apiFetch.
    const body = await res.clone().json().catch(() => null);
    if (body?.detail?.error === 'missing_capability') _onForbidden();
  }
  return res;
}
