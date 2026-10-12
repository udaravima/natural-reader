import { useEffect, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';
import { oldVersionOf } from '../lib/versions';

/**
 * Where the open document stands in the reader's projects (V1 spec §7):
 * fetches GET /v1/docs/{id} when the document changes (and again when
 * refreshKey does, e.g. after a version upload) and returns the banner's
 * item, or null. Quiet on failure — a local-only file, a 404, no backend —
 * because then there is simply nothing to say.
 *
 * The result is stored with the docId it answers, so switching documents
 * shows nothing until the new answer arrives, without a setState in the effect
 * body.
 */
export function useVersionStatus({ apiHost, apiPort, docId, enabled = true, refreshKey = 0 }) {
  const [state, setState] = useState({ key: null, old: null });
  useEffect(() => {
    if (!docId || !enabled) return undefined;
    let live = true;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`);
        if (!res.ok) {
          if (live) setState({ key: docId, old: null });
          return;
        }
        const body = await res.json();
        if (live) setState({ key: docId, old: oldVersionOf(body.versions_in) });
      } catch {
        if (live) setState({ key: docId, old: null }); // offline or no backend: no banner
      }
    })();
    return () => { live = false; };
  }, [apiHost, apiPort, docId, enabled, refreshKey]);
  return enabled && docId && state.key === docId ? state.old : null;
}
