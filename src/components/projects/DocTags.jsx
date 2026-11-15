import { useState } from 'react';
import { Pencil, Sparkles } from 'lucide-react';

/**
 * A project document's description and tags (helper spec §5.4): the project's own words when a Contributor set
 * them, else what the helper model wrote for the current version. A tag filters the list to it (`onTag`).
 * Contributors and above (`canEdit`) also get the pencil, to type tags and a description in place, and, with a
 * helper set up, Generate, which asks the helper again and replaces the project's own words (after a confirm when
 * there are some). Renders nothing when there is nothing to show and nothing to do.
 */
export default function DocTags({ theme, doc, canEdit, helperOn, busy, activeTag, onSave, onGenerate, onTag }) {
  const [mode, setMode] = useState(null); // 'edit' | 'confirm'
  const [tagsText, setTagsText] = useState('');
  const [description, setDescription] = useState('');
  const tags = doc.tags || [];
  const canGenerate = canEdit && helperOn && doc.state === 'indexed' && !doc.describing;
  if (!canEdit && !doc.describing && !doc.description && tags.length === 0) return null;

  const edit = () => {
    setTagsText(tags.join(', '));
    setDescription(doc.description || '');
    setMode('edit');
  };
  const save = async (body) => { if (await onSave(body)) setMode(null); };
  const generate = async () => { setMode(null); await onGenerate(); };
  const chip = (t) => (
    <button key={t} onClick={() => onTag(t)} aria-pressed={activeTag === t} aria-label={`Show documents tagged ${t}`}
      className={`px-1.5 py-0.5 rounded text-[10px] ${activeTag === t ? 'bg-blue-500 text-white' : `${theme.bgTertiary} ${theme.textSecondary} hover:text-blue-500`}`}>
      {t}
    </button>
  );
  const field = `w-full px-2 py-1 rounded border ${theme.border} ${theme.bg}`;

  if (mode === 'edit') {
    return (
      <div className="flex flex-col gap-1 text-[10px]">
        <input type="text" value={tagsText} onChange={(e) => setTagsText(e.target.value)}
          placeholder="Tags, separated by commas" aria-label={`Tags for ${doc.name}`} className={field} />
        <input type="text" maxLength={300} value={description} onChange={(e) => setDescription(e.target.value)}
          placeholder="One line about this document" aria-label={`Description of ${doc.name}`} className={field} />
        <span className="flex flex-wrap gap-2">
          <button disabled={busy} className="px-2 py-0.5 rounded bg-blue-500 text-white disabled:opacity-50"
            onClick={() => save({ tags: tagsText.split(',').map((t) => t.trim()).filter(Boolean), description: description.trim() })}>
            Save
          </button>
          {doc.edited && (
            <button disabled={busy} className="underline disabled:opacity-50" onClick={() => save({ tags: null, description: null })}>
              Use the helper&apos;s
            </button>
          )}
          <button onClick={() => setMode(null)} className="underline">Cancel</button>
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 text-[10px]">
      {doc.description && <p className={`${theme.textSecondary} break-words`}>{doc.description}</p>}
      <div className="flex flex-wrap items-center gap-1">
        {tags.map(chip)}
        {doc.describing && <span className={theme.textMuted}>Writing tags…</span>}
        {canEdit && (
          <button onClick={edit} disabled={busy} aria-label={`Edit tags and description of ${doc.name}`}
            className={`-m-0.5 p-1 hover:text-blue-500 disabled:opacity-50 ${theme.textSecondary}`}>
            <Pencil size={10} />
          </button>
        )}
        {canGenerate && (
          <button onClick={() => (doc.edited ? setMode('confirm') : generate())} disabled={busy}
            aria-label={`Generate tags for ${doc.name}`}
            className={`flex items-center gap-1 hover:text-blue-500 disabled:opacity-50 ${theme.textSecondary}`}>
            <Sparkles size={10} /> Generate
          </button>
        )}
      </div>
      {mode === 'confirm' && (
        <div className="flex flex-wrap items-center gap-2">
          <span className={theme.textSecondary}>Replace this project&apos;s own tags and description with new ones from the helper?</span>
          <button disabled={busy} onClick={generate} className="underline text-red-500 disabled:opacity-50">Replace</button>
          <button onClick={() => setMode(null)} className="underline">Cancel</button>
        </div>
      )}
    </div>
  );
}
