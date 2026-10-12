import { useState } from 'react';
import { BookOpen, ChevronDown, ChevronRight, MoreHorizontal, X } from 'lucide-react';
import VersionHistory from './VersionHistory';

const count = (n) => (n === 1 ? '1 version' : `${n} versions`);

/**
 * One document in a project (V1 spec §6): its current version, a version
 * count that expands into the history, and a ⋯ menu with Upload new version
 * (Contributor+) and Mark as newer version of… (Maintainer+). × removes the
 * document with its history (Maintainer+), after a confirmation.
 */
export default function ProjectDocRow({ theme, api, project, doc, others, mine, busy, run, onOpenDoc }) {
  const { can } = project;
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const [panel, setPanel] = useState(null); // 'upload' | 'merge' | 'remove'
  const [file, setFile] = useState(null);
  const [fromLibrary, setFromLibrary] = useState('');
  const [note, setNote] = useState('');
  const [mergeInto, setMergeInto] = useState('');

  const close = () => { setPanel(null); setFile(null); setFromLibrary(''); setNote(''); setMergeInto(''); };
  const after = (ok) => { if (ok) close(); };
  const choose = (p) => { setMenu(false); setPanel(p); };
  const libraryChoices = mine.filter((m) => m.doc_id !== doc.doc_id);
  const target = others.find((o) => o.id === mergeInto);

  const upload = async () => {
    const label = file ? file.name : libraryChoices.find((m) => m.doc_id === fromLibrary)?.file_name || doc.name;
    after(await run(
      () => (file ? api.uploadVersion(project.id, doc.id, file, note) : api.versionFromLibrary(project.id, doc.id, fromLibrary, note)),
      (out) => (out.restored_from
        ? `That file matched v${out.restored_from}, so it was restored as v${out.version}.`
        : `${label} is now v${out.version}${out.state === 'indexed' ? '' : ' (indexing…)'}.`),
    ));
  };

  return (
    <div className={`flex flex-col gap-1 p-2 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-bold truncate">{doc.name}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}>{doc.state}</span>
          <button onClick={() => setOpen(!open)} aria-label={`History of ${doc.name}`} aria-expanded={open}
            className={`flex items-center text-[10px] ${theme.textSecondary} hover:text-blue-500`}>
            {open ? <ChevronDown size={10} /> : <ChevronRight size={10} />}v{doc.version} · {count(doc.versions_count)}
          </button>
        </span>
        <span className="relative flex items-center gap-2 shrink-0">
          {onOpenDoc && (
            <button onClick={() => onOpenDoc({ doc_id: doc.doc_id, file_name: doc.name })} aria-label={`Open ${doc.name}`}
              className="flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-blue-500 text-white hover:bg-blue-600">
              <BookOpen size={10} /> Open
            </button>
          )}
          {can.file_docs && (
            <button onClick={() => setMenu(!menu)} aria-label={`More actions for ${doc.name}`} aria-haspopup="menu"
              disabled={busy} className={`-m-1 p-1.5 hover:text-blue-500 disabled:opacity-50 ${theme.textSecondary}`}>
              <MoreHorizontal size={12} />
            </button>
          )}
          {menu && (
            <span role="menu" className={`absolute right-0 top-5 z-10 flex flex-col min-w-[12rem] p-1 rounded border shadow ${theme.border} ${theme.bg}`}>
              <button role="menuitem" onClick={() => choose('upload')} className="text-left text-[11px] px-2 py-1 hover:text-blue-500">Upload new version</button>
              {can.remove_docs && others.length > 0 && (
                <button role="menuitem" onClick={() => choose('merge')} className="text-left text-[11px] px-2 py-1 hover:text-blue-500">Mark as newer version of…</button>
              )}
            </span>
          )}
          {can.remove_docs && (
            <button onClick={() => setPanel('remove')} disabled={busy} aria-label={`Remove ${doc.name} from ${project.name}`}
              className={`-m-1 p-1.5 hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
              <X size={12} />
            </button>
          )}
        </span>
      </div>

      {panel === 'upload' && (
        <div className="flex flex-col gap-1 text-[10px]">
          <div className="flex flex-wrap items-center gap-2">
            <input type="file" accept=".pdf,.txt,.md,.markdown" aria-label={`Choose a file for the new version of ${doc.name}`}
              onChange={(e) => { setFile(e.target.files?.[0] || null); setFromLibrary(''); }} className="max-w-full text-[10px]" />
            {libraryChoices.length > 0 && (
              <select value={fromLibrary} aria-label="New version from my library"
                onChange={(e) => { setFromLibrary(e.target.value); setFile(null); }}
                className={`max-w-[12rem] px-1.5 py-0.5 rounded border ${theme.border} ${theme.bg}`}>
                <option value="">…or from my library</option>
                {libraryChoices.map((m) => <option key={m.doc_id} value={m.doc_id}>{m.file_name}</option>)}
              </select>
            )}
          </div>
          <input type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="What changed? (optional)" aria-label="What changed?"
            className={`w-full px-2 py-1 rounded border ${theme.border} ${theme.bg}`} />
          <span className="flex gap-2">
            <button disabled={busy || (!file && !fromLibrary)} onClick={upload}
              className="px-2 py-0.5 rounded bg-blue-500 text-white disabled:opacity-50">Upload</button>
            <button onClick={close} className="underline">Cancel</button>
          </span>
        </div>
      )}

      {panel === 'merge' && (
        <div className="flex flex-col gap-1 text-[10px]">
          <select value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}
            aria-label={`Mark ${doc.name} as a newer version of`}
            className={`self-start max-w-full px-1.5 py-0.5 rounded border ${theme.border} ${theme.bg}`}>
            <option value="">Pick the document it replaces…</option>
            {/* Version and last uploader tell same-named duplicates apart, the usual merge case. */}
            {others.map((o) => (
              <option key={o.id} value={o.id}>
                {[o.name, `v${o.version}`, o.updated_by?.name].filter(Boolean).join(' · ')}
              </option>
            ))}
          </select>
          {target && (
            <>
              <span className={theme.textSecondary}>
                {doc.name} becomes v{target.version + doc.versions_count} of {target.name}, and the row will be called {doc.name}.
                {' '}This can&apos;t be undone; every file stays in the history.
              </span>
              <span className="flex gap-2">
                <button disabled={busy} className="underline text-red-500"
                  onClick={async () => after(await run(() => api.mergeDocuments(project.id, target.id, doc.id), `Merged ${doc.name} into ${target.name}.`))}>
                  Confirm merge
                </button>
                <button onClick={close} className="underline">Cancel</button>
              </span>
            </>
          )}
        </div>
      )}

      {panel === 'remove' && (
        <div className="flex flex-wrap items-center gap-2 text-[10px]">
          <span className={theme.textSecondary}>
            {doc.versions_count > 1
              ? `Remove ${doc.name} and its ${doc.versions_count} versions from ${project.name}?`
              : `Remove ${doc.name} from ${project.name}?`}
            {' '}If nobody else has it in their library, it is deleted.
          </span>
          <button disabled={busy} className="underline text-red-500 disabled:opacity-50"
            onClick={async () => after(await run(() => api.removeDocument(project.id, doc.id), `Removed ${doc.name}.`))}>
            Confirm remove
          </button>
          <button onClick={close} className="underline">Cancel</button>
        </div>
      )}

      {open && (
        <VersionHistory theme={theme} api={api} project={project} doc={doc}
          busy={busy} run={run} onOpenDoc={onOpenDoc} />
      )}
    </div>
  );
}
