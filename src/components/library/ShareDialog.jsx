import { useCallback, useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import PeoplePicker from '../people/PeoplePicker';

/**
 * Share one document I uploaded with one person at a time (A0 §10, journey
 * 6). Shares are read-only and can't be passed on (A1 §5). Lists the people
 * I already shared it with, each removable.
 */
export default function ShareDialog({ theme, api, doc, showToast, onClose }) {
  const [shares, setShares] = useState(null);
  const [busyId, setBusyId] = useState(null);
  // Set when the share list fails to load; distinct from a genuinely empty list.
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const list = await api.shares(doc.doc_id);
      setShares(list);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [api, doc.doc_id]);

  useEffect(() => { load(); }, [load]);

  const run = async (key, fn, ok) => {
    setBusyId(key);
    try {
      await fn();
      showToast(ok, 3000);
    } catch (e) {
      showToast(e.message, 5000);
    } finally {
      setBusyId(null);
      await load();
    }
  };

  return (
    <div role="dialog" aria-label={`Share ${doc.file_name}`} className={`flex flex-col gap-2 p-2 rounded border ${theme.border} ${theme.bg}`}>
      <div className="flex items-center justify-between text-xs">
        <span className="font-bold">Share “{doc.file_name}”</span>
        <button onClick={onClose} aria-label="Close sharing" className={theme.textSecondary}><X size={12} /></button>
      </div>
      <p className={`text-[10px] ${theme.textMuted}`}>People you share with can read this document. They can't share it on.</p>
      <PeoplePicker theme={theme} lookup={api.lookup} label={`Share ${doc.file_name} with`} disabled={busyId !== null}
        excludeIds={(shares || []).map((s) => s.user_id)} excludedLabel="Already shared with them."
        onPick={(p) => run(p.id, () => api.share(doc.doc_id, p.id), `Shared ${doc.file_name} with ${p.name}.`)} />
      {error ? (
        <div role="alert" className="flex items-center gap-2 text-[10px] text-red-500">
          <span>{error}</span>
          <button onClick={load} className="underline">Retry</button>
        </div>
      ) : shares === null ? (
        <p className={`text-[10px] ${theme.textMuted}`}>Loading…</p>
      ) : shares.length === 0 ? (
        <p className={`text-[10px] ${theme.textMuted}`}>Not shared with anyone yet.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {shares.map((s) => (
            <li key={s.user_id} className="flex items-center justify-between gap-2 text-xs">
              <span>
                {s.name}
                {s.username && <span className={`ml-1 text-[10px] ${theme.textMuted}`}>@{s.username}</span>}
              </span>
              <button onClick={() => run(s.user_id, () => api.unshare(doc.doc_id, s.user_id), `Stopped sharing with ${s.name}.`)}
                disabled={busyId !== null} aria-label={`Stop sharing with ${s.name}`}
                className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
                {busyId === s.user_id ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
