import { useCallback, useEffect, useRef, useState } from 'react';
import ProjectDocRow from './ProjectDocRow';
import NameMatchDialog from './NameMatchDialog';
import BulkUploadPanel from './BulkUploadPanel';
import { useProjectFiling } from '../../hooks/useProjectFiling';
import { useBulkUpload } from '../../hooks/useBulkUpload';
import { isMoving } from '../../lib/docStateLabel';
import { filesFromDataTransfer, filesFromInput } from '../../lib/folderDrop';

// While a row is queued or being indexed, the list is re-read so its badge
// follows it to Indexed or Failed (V1 §6). One read at a time. A batch waits
// in the server's queue longer than ten minutes on a machine with no GPU, so
// the watch goes on while anything moves, slower after the first 200 reads; a
// document nothing will index (no job) doesn't keep it going.
const POLL_MS = 3000;
const SLOW_POLL_MS = 15000;
const FAST_POLLS = 200; // ~10 minutes

/**
 * A project's documents (A0 §10, V1 §6): one row per document — its current
 * version — with the history underneath. Contributors file their own uploads
 * in, upload new versions and restore old ones; Maintainers and Owners also
 * merge documents, delete old versions and remove documents, after a
 * confirmation. Filing a name or file the project already has asks first.
 * An admin who isn't a member sees counts only (A0 §5), so this says so.
 * Contributors also upload many files at once (Upload files, Upload folder, or a drop anywhere on the tab): the
 * plan shows what each becomes before anything is sent (BulkUploadPanel).
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

  const onLanded = useCallback(() => { load(); onChanged?.(); }, [load, onChanged]);
  const batch = useBulkUpload({ api, projectId: project.id, onLanded });
  const canPick = can.file_docs && batch.phase !== 'reading' && batch.phase !== 'sending';
  const filesInput = useRef(null);
  const folderInput = useRef(null);
  // React passes no boolean for an attribute it doesn't know; the folder picker needs webkitdirectory present.
  useEffect(() => { folderInput.current?.setAttribute('webkitdirectory', ''); }, [can.file_docs]);
  const picked = (e) => {
    const items = filesFromInput(e.target.files); // copied before the input is cleared, so the same pick works twice
    e.target.value = '';
    if (items.length) batch.pick(items);
  };
  const [dragging, setDragging] = useState(false);
  const carriesFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
  const dropTarget = canPick ? {
    onDragOver: (e) => { if (carriesFiles(e)) { e.preventDefault(); setDragging(true); } },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); },
    onDrop: (e) => {
      e.preventDefault();
      setDragging(false);
      filesFromDataTransfer(e.dataTransfer).then((items) => { if (items.length) batch.pick(items); });
    },
  } : {};

  const settling = (docs || []).some(isMoving);
  const [pollTick, setPollTick] = useState(0);
  const polls = useRef(0);
  useEffect(() => {
    if (!settling) { polls.current = 0; return undefined; }
    let live = true;
    // The next read is scheduled only after this one finishes: no overlap.
    const timer = setTimeout(async () => {
      polls.current += 1;
      await load();
      if (live) setPollTick((n) => n + 1);
    }, polls.current < FAST_POLLS ? POLL_MS : SLOW_POLL_MS);
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
  const pickButton = `px-2 py-1 text-xs rounded border disabled:opacity-50 ${theme.border} ${theme.bg}`;
  return (
    <div role="region" aria-label={`Documents in ${project.name}`} {...dropTarget}
      className={`relative flex flex-col gap-2 ${dragging ? 'outline-dashed outline-2 outline-blue-500 rounded' : ''}`}>
      {dragging && (
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded bg-blue-500/10 text-xs text-blue-500">
          Drop to upload to &quot;{project.name}&quot;
        </div>
      )}
      {can.file_docs && (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            {unfiled.length > 0 && (
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
            <button onClick={() => filesInput.current?.click()} disabled={!canPick} className={pickButton}>Upload files</button>
            <button onClick={() => folderInput.current?.click()} disabled={!canPick} className={pickButton}>Upload folder</button>
            <input ref={filesInput} type="file" multiple accept=".pdf,.md,.markdown,.txt" hidden
              aria-label="Files to upload" onChange={picked} />
            <input ref={folderInput} type="file" multiple hidden aria-label="Folder to upload" onChange={picked} />
          </div>
          <span className={`text-[10px] ${theme.textMuted}`}>…or drop files or a folder anywhere on this tab</span>
        </div>
      )}
      <BulkUploadPanel theme={theme} project={project} batch={batch} />
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
