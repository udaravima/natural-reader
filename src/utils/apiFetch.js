import { buildApiUrl } from './url';

// A single process-wide handler so any 401 — from any call site — can drop the
// app back to the login gate without threading a callback through every caller.
let _onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  _onUnauthorized = fn;
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
  return res;
}
