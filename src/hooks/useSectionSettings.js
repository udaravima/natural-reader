import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';

/**
 * One section of the admin console's settings (GET/PUT /v1/admin/settings/{section}).
 *
 * The server answers with, per setting, the value in force, where it comes from
 * ('admin' | 'env' | 'default'), the env variable behind it, and the `fallback` that
 * applies once the console's own choice is removed. This hook keeps what the admin
 * has changed on screen as drafts, so a section saves with one Save instead of one
 * request per control, and reading a drafted row already shows the effect of the
 * change (a reset shows the fallback and its source, before anything is sent).
 *
 * A hook instance belongs to one mounted section: drafts live until a save or
 * discard(), and are not cleared if `section` changes under it.
 */

const USE_SERVER = Symbol('use the server setting');

// Values here are scalars and arrays of scalars (an allow-list), so JSON is enough to compare them.
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function refusal(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail === 'string' && detail) return detail;
    if (typeof detail?.message === 'string' && detail.message) return detail.message;
  } catch { /* not JSON */ }
  return `HTTP ${res.status}`;
}

export function useSectionSettings(apiHost, apiPort, section) {
  const path = `/v1/admin/settings/${encodeURIComponent(section)}`;
  // Tagged with the request it answers, so a section or server change reads as loading again.
  const key = `${apiHost}|${apiPort}|${path}`;
  const [loaded, setLoaded] = useState(null);   // { key, settings, info } or { key, failed: true }
  const [drafts, setDrafts] = useState({});     // name -> a value, or USE_SERVER
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const inFlight = useRef(false);   // checked synchronously: `saving` is a render-time value and lags a second call in the same tick

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, path);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        if (!cancelled) setLoaded({ key, settings: body.settings, info: body.info ?? [] });
      } catch {
        if (!cancelled) setLoaded({ key, failed: true });
      }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort, path, key]);

  const current = loaded?.key === key ? loaded : null;
  const status = !current ? 'loading' : current.failed ? 'failed' : 'ready';
  const server = (name) => current?.settings?.[name];

  const set = (name, v) => {
    const s = server(name);
    setDrafts((d) => {
      const next = { ...d };
      // Back to what is already stored in the console: nothing to send.
      if (s && s.source === 'admin' && same(s.value, v)) delete next[name];
      else next[name] = v;
      return next;
    });
  };

  const useServer = (name) => {
    const s = server(name);
    setDrafts((d) => {
      const next = { ...d };
      // A row the console never set already follows the server: nothing to remove.
      if (s && s.source !== 'admin') delete next[name];
      else next[name] = USE_SERVER;
      return next;
    });
  };

  const value = (name) => {
    const s = server(name);
    if (name in drafts) return drafts[name] === USE_SERVER ? s?.fallback : drafts[name];
    return s?.value;
  };
  const source = (name) => {
    const s = server(name);
    if (name in drafts) return drafts[name] === USE_SERVER ? s?.fallback_source : 'admin';
    return s?.source;
  };

  const save = async () => {
    if (inFlight.current) return false;   // one request at a time; the one in flight decides
    const sent = { ...drafts };           // what this request carries; the admin may keep editing while it is out
    const names = Object.keys(sent);
    if (!names.length) return true;
    const changes = Object.fromEntries(names.map((n) => [n, sent[n] === USE_SERVER ? null : sent[n]]));
    inFlight.current = true;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(apiHost, apiPort, path, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes }),
      });
      if (!res.ok) {
        setError(await refusal(res));
        return false;
      }
      const body = await res.json();
      setLoaded({ key, settings: body.settings, info: body.info ?? [] });
      // Drop only what was saved: a name edited again, or first edited, during the request stays drafted.
      setDrafts((d) => {
        const next = { ...d };
        for (const n of names) if (n in next && (next[n] === sent[n] || same(next[n], sent[n]))) delete next[n];
        return next;
      });
      return true;
    } catch (e) {
      setError(e.message || 'The request failed.');
      return false;
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  return {
    status,
    info: current?.info ?? [],
    value,
    source,
    fallback: (name) => server(name)?.fallback,
    envName: (name) => server(name)?.env,
    set,
    useServer,
    canUseServer: (name) => source(name) === 'admin',
    dirty: Object.keys(drafts).length > 0,
    saving,
    error,
    save,
    discard: () => { setDrafts({}); setError(null); },
  };
}
