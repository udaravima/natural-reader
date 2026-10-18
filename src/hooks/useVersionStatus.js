import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';
import { oldVersionOf } from '../lib/versions';

const NONE = Object.freeze([]);   // one array for "no projects", so a prop made from it stays equal between renders

/**
 * Where the open document stands in the reader's projects (V1 spec §7):
 * fetches GET /v1/docs/{id} when the document changes (and again when
 * refreshKey does, e.g. after a version upload) and returns
 *   - `old`: the banner's item, or null;
 *   - `projects`: [{id, name}] — the projects the document is the current
 *     version in, that the reader belongs to (the response's `projects`), which
 *     the chat's scope menu offers; [] when unknown.
 * Quiet on failure — a local-only file, a 404, no backend — because then there
 * is simply nothing to say.
 *
 * The result is stored with the docId it answers, so switching documents
 * shows nothing until the new answer arrives, without a setState in the effect
 * body.
 */
export function useVersionStatus({ apiHost, apiPort, docId, enabled = true, refreshKey = 0 }) {
  const [state, setState] = useState({ key: null, old: null, projects: NONE });
  useEffect(() => {
    if (!docId || !enabled) return undefined;
    let live = true;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`);
        if (!res.ok) {
          if (live) setState({ key: docId, old: null, projects: NONE });
          return;
        }
        const body = await res.json();
        const projects = Array.isArray(body.projects) && body.projects.length
          ? body.projects.map((p) => ({ id: p.id, name: p.name }))
          : NONE;
        if (live) setState({ key: docId, old: oldVersionOf(body.versions_in), projects });
      } catch {
        if (live) setState({ key: docId, old: null, projects: NONE }); // offline or no backend: no banner
      }
    })();
    return () => { live = false; };
  }, [apiHost, apiPort, docId, enabled, refreshKey]);
  const answered = enabled && docId && state.key === docId;
  const { old, projects } = state;
  return useMemo(
    () => (answered ? { old, projects } : { old: null, projects: NONE }),
    [answered, old, projects],
  );
}
