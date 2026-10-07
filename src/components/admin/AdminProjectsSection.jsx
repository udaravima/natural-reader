import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { projectsApi } from '../../lib/projectsApi';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/**
 * Every project on the server (A0 §10): owners, counts, an "Ownerless only"
 * filter, Open (the project page with admin powers) and, for a project whose
 * last Owner was deleted, "Add me as Owner" — the recovery path. The server
 * logs an admin adding themselves at WARNING.
 */
export function AdminProjectsSection({ theme, apiHost, apiPort, currentUserId, showToast, onOpenProject }) {
  const api = useMemo(() => projectsApi(apiHost, apiPort), [apiHost, apiPort]);
  const [ownerlessOnly, setOwnerlessOnly] = useState(false);
  const [rows, setRows] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const reqId = useRef(0); // only the latest request may set rows/error

  const load = useCallback(async () => {
    const id = ++reqId.current;
    try {
      const data = await api.adminProjects(ownerlessOnly);
      if (id !== reqId.current) return;
      setRows(data);
      setError(null);
    } catch (e) {
      if (id !== reqId.current) return;
      setError(e.message);
    }
  }, [api, ownerlessOnly]);

  useEffect(() => { load(); }, [load]);

  const recover = async (p) => {
    setBusyId(p.id);
    try {
      await api.putMember(p.id, currentUserId, 'owner');
      showToast(`You're now an Owner of ${p.name}.`, 4000);
    } catch (e) {
      showToast(e.message, 5000);
    } finally {
      setBusyId(null);
      await load();
    }
  };

  return (
    <section className="flex flex-col gap-3" aria-label="Projects">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-bold uppercase tracking-wider text-blue-500">Projects</h2>
        <label className="flex items-center gap-1 text-[10px]">
          <input type="checkbox" checked={ownerlessOnly} onChange={(e) => setOwnerlessOnly(e.target.checked)} aria-label="Ownerless only" />
          Ownerless only
        </label>
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-500 flex items-center gap-2">
          Could not load projects: {error}
          <button onClick={load} className="underline">Retry</button>
        </p>
      )}
      {rows === null ? (
        error ? null : <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : rows.length === 0 && error ? null : rows.length === 0 ? (
        <p className={`text-xs ${theme.textMuted}`}>{ownerlessOnly ? 'No ownerless projects.' : 'No projects yet.'}</p>
      ) : rows.map((p) => (
        <div key={p.id} className={`flex items-center justify-between gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary} text-xs`}>
          <span className="flex flex-col gap-0.5 min-w-0">
            <span className="font-bold truncate">{p.name}</span>
            <span className={`text-[10px] ${theme.textMuted}`}>
              {p.ownerless
                ? <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">No owner</span>
                : p.owners.map((o) => o.name).join(', ')}
            </span>
            <span className={`text-[10px] ${theme.textMuted}`}>{plural(p.member_count, 'member')} · {plural(p.doc_count, 'document')}</span>
          </span>
          <span className="flex items-center gap-2 shrink-0">
            <button onClick={() => onOpenProject(p.id)} aria-label={`Open ${p.name}`} className="underline">Open</button>
            {p.ownerless && (
              <button onClick={() => recover(p)} disabled={busyId !== null} aria-label={`Add me as Owner of ${p.name}`}
                className="underline text-green-600 disabled:opacity-50">
                {busyId === p.id ? <Loader2 size={12} className="animate-spin" /> : 'Add me as Owner'}
              </button>
            )}
          </span>
        </div>
      ))}
    </section>
  );
}
