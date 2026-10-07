import { useCallback, useEffect, useState } from 'react';
import { BookOpen, Loader2, X } from 'lucide-react';

/**
 * A project's documents (A0 §10). Contributors and above file their own
 * uploads in; Maintainers and Owners remove any — after a confirmation,
 * because content nobody else holds is deleted with its last placement.
 * An admin who isn't a member sees counts only (§5), so this says so.
 */
export default function ProjectDocsTab({ theme, api, project, showToast, onOpenDoc, onRefused, onChanged }) {
  const [docs, setDocs] = useState(null);
  const [error, setError] = useState(null);
  const [mine, setMine] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const member = project.my_role !== null;
  const { can } = project;

  const load = useCallback(async () => {
    if (!member) return;
    try {
      const [inProject, all] = await Promise.all([
        api.projectDocs(project.id),
        can.file_docs ? api.myDocs() : Promise.resolve([]),
      ]);
      setDocs(inProject);
      setMine(all.filter((d) => d.added_via === 'upload' && !(d.projects || []).some((p) => p.id === project.id)));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [api, project.id, can.file_docs, member]);

  useEffect(() => { load(); }, [load]);

  const run = async (docId, fn, ok) => {
    setBusyId(docId);
    try {
      await fn();
      if (ok) showToast(ok, 3000);
      onChanged?.();
    } catch (e) {
      showToast(e.message, 5000);
      onRefused?.();
    } finally {
      setBusyId(null);
      setConfirmId(null);
      await load();
    }
  };

  if (!member) {
    return <p className={`text-xs ${theme.textMuted}`}>Only members can see this project's documents.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {can.file_docs && mine.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            const d = mine.find((x) => x.doc_id === e.target.value);
            if (d) run(d.doc_id, () => api.fileDoc(project.id, d.doc_id), `Filed ${d.file_name} into ${project.name}.`);
          }}
          disabled={busyId !== null}
          aria-label="File a document into this project"
          className={`self-start px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
        >
          <option value="">File a document…</option>
          {mine.map((d) => <option key={d.doc_id} value={d.doc_id}>{d.file_name}</option>)}
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
        <div key={d.doc_id} className={`flex flex-col gap-1 p-2 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 min-w-0">
              <span className="text-xs font-bold truncate">{d.file_name}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}>{d.state}</span>
            </span>
            <span className="flex items-center gap-2 shrink-0">
              {onOpenDoc && (
                <button onClick={() => onOpenDoc(d)} aria-label={`Open ${d.file_name}`}
                  className="flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-blue-500 text-white hover:bg-blue-600">
                  <BookOpen size={10} /> Open
                </button>
              )}
              {can.remove_docs && (
                <button onClick={() => setConfirmId(d.doc_id)} disabled={busyId !== null}
                  aria-label={`Remove ${d.file_name} from ${project.name}`}
                  className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
                  {busyId === d.doc_id ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                </button>
              )}
            </span>
          </div>
          {confirmId === d.doc_id && (
            <div className="flex flex-wrap items-center gap-2 text-[10px]">
              <span className={theme.textSecondary}>
                Remove from {project.name}? If nobody else has it in their library, it is deleted.
              </span>
              <button onClick={() => run(d.doc_id, () => api.unfileDoc(project.id, d.doc_id), `Removed ${d.file_name}.`)}
                className="underline text-red-500">Confirm remove</button>
              <button onClick={() => setConfirmId(null)} className="underline">Cancel</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
