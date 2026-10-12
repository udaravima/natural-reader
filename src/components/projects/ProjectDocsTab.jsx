import { useCallback, useEffect, useRef, useState } from 'react';
import ProjectDocRow from './ProjectDocRow';
import NameMatchDialog from './NameMatchDialog';
import { useProjectFiling } from '../../hooks/useProjectFiling';
import { PROCESSING_STATES } from '../../lib/docStatusPoller';

// While a row's content is still being indexed, the list is re-read so its
// badge follows the state to indexed or failed (V1 §6). One read at a time;
// capped, like the reader's index poll, so content stuck in `stored` (an
// interrupted upload waiting for Index) doesn't poll for ever.
const POLL_MS = 3000;
const MAX_POLLS = 200; // ~10 minutes

/**
 * A project's documents (A0 §10, V1 §6): one row per document — its current
 * version — with the history underneath. Contributors file their own uploads
 * in, upload new versions and restore old ones; Maintainers and Owners also
 * merge documents, delete old versions and remove documents, after a
 * confirmation. Filing a name or file the project already has asks first.
 * An admin who isn't a member sees counts only (A0 §5), so this says so.
 */
export default function ProjectDocsTab({ theme, api, project, showToast, onOpenDoc, onRefused, onChanged }) {
  const [docs, setDocs] = useState(null);
  const [error, setError] = useState(null);
  const [mine, setMine] = useState([]);
  const [busy, setBusy] = useState(false);
  const member = project.my_role !== null;
  const { can } = project;

  const load = useCallback(async () => {
    if (!member) return;
    try {
      const [inProject, all] = await Promise.all([
        api.documents(project.id),
        can.file_docs ? api.myDocs() : Promise.resolve([]),
      ]);
      setDocs(inProject);
      setMine(all.filter((d) => d.added_via === 'upload'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [api, project.id, can.file_docs, member]);

  useEffect(() => { load(); }, [load]);

  const settling = (docs || []).some((d) => PROCESSING_STATES.has(d.state));
  const [pollTick, setPollTick] = useState(0);
  const polls = useRef(0);
  useEffect(() => {
    if (!settling) { polls.current = 0; return undefined; }
    if (polls.current >= MAX_POLLS) return undefined;
    let live = true;
    // The next read is scheduled only after this one finishes: no overlap.
    const timer = setTimeout(async () => {
      polls.current += 1;
      await load();
      if (live) setPollTick((n) => n + 1);
    }, POLL_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [settling, pollTick, load]);

  // One action at a time across the tab. Any refusal reloads, because our
  // role may have changed; `ok` is a message or a function of the result.
  const run = useCallback(async (fn, ok) => {
    setBusy(true);
    try {
      const out = await fn();
      const message = typeof ok === 'function' ? ok(out) : ok;
      if (message) showToast(message, 3000);
      onChanged?.();
      return true;
    } catch (e) {
      showToast(e.message, 5000);
      onRefused?.();
      return false;
    } finally {
      setBusy(false);
      await load();
    }
  }, [load, onChanged, onRefused, showToast]);

  const filing = useProjectFiling({
    fileDoc: api.fileDoc,
    onFiled: (_filed, t) => { showToast(`Filed ${t.label} into ${project.name}.`, 3000); onChanged?.(); load(); },
    onError: (message) => { showToast(message, 5000); onRefused?.(); load(); },
  });

  if (!member) {
    return <p className={`text-xs ${theme.textMuted}`}>Only members can see this project&apos;s documents.</p>;
  }
  const unfiled = mine.filter((d) => !(d.projects || []).some((p) => p.id === project.id));
  return (
    <div className="flex flex-col gap-2">
      {can.file_docs && unfiled.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            const d = unfiled.find((x) => x.doc_id === e.target.value);
            if (d) filing.file({ projectId: project.id, projectName: project.name, docId: d.doc_id, label: d.file_name });
          }}
          disabled={busy || filing.busy}
          aria-label="File a document into this project"
          className={`self-start px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
        >
          <option value="">File a document…</option>
          {unfiled.map((d) => <option key={d.doc_id} value={d.doc_id}>{d.file_name}</option>)}
        </select>
      )}
      {error && (
        <div role="alert" className="flex items-center gap-2 text-xs text-red-500">
          <span>{error}</span>
          <button onClick={load} className="underline">Retry</button>
        </div>
      )}
      {docs === null ? (
        error ? null : <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : docs.length === 0 ? (
        <p className={`text-xs ${theme.textMuted}`}>No documents in this project yet.</p>
      ) : docs.map((d) => (
        <ProjectDocRow key={d.id} theme={theme} api={api} project={project} doc={d}
          others={docs.filter((o) => o.id !== d.id)} mine={mine} busy={busy} run={run} onOpenDoc={onOpenDoc} />
      ))}
      {filing.dialog && <NameMatchDialog theme={theme} {...filing.dialog} />}
    </div>
  );
}
