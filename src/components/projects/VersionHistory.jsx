import { useEffect, useState } from 'react';
import { BookOpen } from 'lucide-react';

const originLabel = (v) => (v.origin === 'restore' ? `restored from v${v.origin_version}`
  : v.origin === 'merge' ? `merged (was v${v.origin_version})` : null);

/**
 * A document's versions, newest first (V1 spec §6). Every line opens in the
 * reader; an old line offers Restore (Contributor+) and Delete (Maintainer+,
 * after an inline confirmation). It refetches whenever the row above it
 * reports a new version or version count, so a change made from anywhere (this
 * row, another row's merge, the filing dialog) shows up; `run(fn, ok)` is the
 * tab's one-action-at-a-time runner.
 */
export default function VersionHistory({ theme, api, project, doc, busy, run, onOpenDoc }) {
  const { can } = project;
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState(null);
  const [confirm, setConfirm] = useState(null);

  useEffect(() => {
    let live = true;
    api.versions(project.id, doc.id)
      .then((v) => { if (live) { setVersions(v); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [api, project.id, doc.id, doc.version, doc.versions_count]);

  const act = async (fn, ok) => {
    if (await run(fn, ok)) setConfirm(null);
  };

  if (error) return <p role="alert" className="text-[10px] text-red-500">{error}</p>;
  if (versions === null) return <p className={`text-[10px] ${theme.textMuted}`}>Loading history…</p>;
  return (
    <ul className="flex flex-col gap-1 pl-2 border-l border-dashed">
      {versions.map((v) => (
        <li key={v.version} data-testid={`version-${v.version}`} className="flex flex-col gap-0.5 text-[10px]">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-bold">v{v.version}</span>
            {v.is_current && <span className="px-1 rounded bg-green-500/10 text-green-600">current</span>}
            <span className="truncate max-w-[12rem]">{v.file_name}</span>
            <span className={theme.textSecondary}>
              {v.added_by?.name || 'a former member'} · {new Date(v.added_at).toLocaleDateString()}
            </span>
            {v.note && <span className={theme.textSecondary}>&quot;{v.note}&quot;</span>}
            {originLabel(v) && <span className={theme.textMuted}>{originLabel(v)}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => onOpenDoc?.({ doc_id: v.doc_id, file_name: v.file_name })}
              aria-label={`Open v${v.version}`} className="flex items-center gap-1 underline">
              <BookOpen size={10} /> Open
            </button>
            {!v.is_current && can.file_docs && (
              <button disabled={busy} aria-label={`Restore v${v.version}`} className="underline disabled:opacity-50"
                onClick={() => act(() => api.restoreVersion(project.id, doc.id, v.version),
                  (out) => `Restored v${v.version} as v${out.version}.`)}>
                Restore
              </button>
            )}
            {!v.is_current && can.remove_docs && (confirm === v.version ? (
              <span className="flex flex-wrap items-center gap-2">
                <span className={theme.textSecondary}>Delete v{v.version} for good? Its file goes if nothing else uses it.</span>
                <button disabled={busy} className="underline text-red-500 disabled:opacity-50"
                  onClick={() => act(() => api.deleteVersion(project.id, doc.id, v.version), `Deleted v${v.version}.`)}>
                  Confirm delete
                </button>
                <button className="underline" onClick={() => setConfirm(null)}>Cancel</button>
              </span>
            ) : (
              <button disabled={busy} aria-label={`Delete v${v.version}`} className="underline text-red-500 disabled:opacity-50"
                onClick={() => setConfirm(v.version)}>
                Delete
              </button>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}
