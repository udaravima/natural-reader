import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { CharacterDraft } from './CharacterDraft';
import { TryProfile } from './TryProfile';
import { useChatModels } from './useChatModels';

const fmt = (n) => n.toLocaleString('en-US');
const PREVIEW_DELAY_MS = 300;   // after the last keystroke, while the preview is shown
const BACKSTORY_MAX = 6000;     // characters, when the server doesn't say
// The label the server puts before a backstory (chat/profile_text.BACKSTORY_LABEL), counted with it.
const BACKSTORY_LABEL_CHARS = 96;

/**
 * One profile of the library, new (`initial.id` null) or saved (profile library spec, "For an admin"): its name,
 * the line people see under it, its backstory and its rules, read after the house rules (character profiles spec).
 *
 * Generate writes a whole character from the admin's notes with the model they pick; it fills the backstory and the
 * rules, and the name and description only where they are empty. Nothing is saved until Save profile, and unsaved
 * text is replaced only after a confirm. "Write the description" asks the helper for the line people see. Try it
 * asks the unsaved profile one question.
 *
 * "What the model receives" asks the server for the full system message with this text, unsaved, and the saved
 * house rules, placeholders filled in; it follows the typing while it is shown, and `refreshKey` (a setting saved
 * elsewhere in the console) fetches it again.
 */
export function ProfileEditor({ theme, apiHost, apiPort, showToast, initial, limits, houseText, helperOn, busy,
  refreshKey = 0, onSave, onCancel, onDirtyChange, builtin = false }) {
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [text, setText] = useState(initial.text);
  const [backstory, setBackstory] = useState(initial.backstory ?? '');
  const [describing, setDescribing] = useState(false);
  const models = useChatModels(apiHost, apiPort);
  const [warnings, setWarnings] = useState([]);      // from the last draft or preview, until the text is edited
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState(null);

  const dirty = name !== initial.name || description !== initial.description || text !== initial.text
    || backstory !== (initial.backstory ?? '');
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  // What the fields hold now, for a reply that arrives after the admin kept typing.
  const now = useRef({ name, description, text, backstory });
  useEffect(() => { now.current = { name, description, text, backstory }; }, [name, description, text, backstory]);

  useEffect(() => {
    if (!previewOpen) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, backstory, text }),
        });
        if (!res.ok || cancelled) return;
        const body = await res.json();
        if (cancelled) return;
        setPreview(body.preview);
        setWarnings(body.warnings || []);
      } catch { /* the preview stays as it was */ }
    }, PREVIEW_DELAY_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [previewOpen, name, backstory, text, refreshKey, apiHost, apiPort]);

  // Generate's reply: the backstory and rules replace what is there (unsaved text only after a confirm); the name
  // and description fill only empty fields, so a saved name stays.
  const applyDraft = (draft) => {
    const at = now.current;
    const unsaved = (at.text.trim() && at.text !== initial.text)
      || (at.backstory.trim() && at.backstory !== (initial.backstory ?? ''));
    if (unsaved && !window.confirm('Replace the unsaved backstory and rules in the editor with the draft?')) return;
    if (!at.name.trim() && draft.name) setName(draft.name);
    if (!at.description.trim() && draft.description) setDescription(draft.description);
    setBackstory(draft.backstory || '');
    setText(draft.text || '');
    setWarnings(draft.warnings || []);
    showToast?.('Draft placed in the editor. Nothing is saved until you press Save profile.', 5000);
  };

  const writeDescription = async () => {
    if (describing) return;
    setDescribing(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/describe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, backstory, text }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast?.(body?.detail?.message || `Writing the description failed (HTTP ${res.status}).`, 5000);
        return;
      }
      setDescription(body.description || '');
    } catch {
      showToast?.('The server could not be reached.', 5000);
    } finally {
      setDescribing(false);
    }
  };

  const cancel = () => {
    if (dirty && !window.confirm('Discard the unsaved changes to this profile?')) return;
    onCancel();
  };

  const parts = [houseText.length, backstory ? BACKSTORY_LABEL_CHARS + backstory.length : 0, text.length].filter(Boolean);
  const combined = parts.reduce((a, b) => a + b, 0) + 2 * Math.max(0, parts.length - 1);
  const withWhat = [houseText && 'house rules', backstory && 'backstory'].filter(Boolean).join(' and ');
  const shown = [...new Set([...(dirty ? [] : initial.warnings || []), ...warnings])];
  const field = `min-h-11 md:min-h-0 w-full text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`;
  const link = 'min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline';

  return (
    <div className={`flex flex-col gap-2 p-3 rounded border ${theme.border}`}>
      <h3 className="text-xs font-bold">{initial.id ? `Edit ${initial.name}` : 'New profile'}</h3>
      <div className="flex flex-col md:flex-row gap-2">
        <input aria-label="Profile name" value={name} maxLength={limits.name} onChange={(e) => setName(e.target.value)}
          placeholder="Tutor" className={`md:w-48 ${field}`} />
        <input aria-label="Profile description" value={description} maxLength={limits.description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Explains step by step (people see this under the name)" className={field} />
        {!builtin && helperOn && (
          <button onClick={writeDescription} disabled={describing || (!backstory.trim() && !text.trim())}
            aria-label="Write the description" title="The helper model writes the line people see, from this profile"
            className={`shrink-0 text-xs font-bold ${link}`}>
            {describing ? 'Writing…' : '✦ Generate'}
          </button>
        )}
      </div>
      {!builtin && (
        <CharacterDraft theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast} models={models}
          helperOn={Boolean(helperOn)} onDraft={applyDraft} />
      )}
      {builtin ? (
        <p className={`text-xs ${theme.textMuted}`}>
          The Librarian has no text of its own: it answers with the app&apos;s rules about documents, after the house
          rules. You can rename it and change the line people see under its name.
        </p>
      ) : (<>
      <label htmlFor="assistant-backstory" className={`text-xs font-bold ${theme.textSecondary}`}>Backstory</label>
      <textarea id="assistant-backstory" aria-label="Backstory" rows={8} maxLength={limits.backstory ?? BACKSTORY_MAX}
        value={backstory} onChange={(e) => { setBackstory(e.target.value); setWarnings([]); }}
        placeholder="Who the character is: their past, their voice, the words they use, what they care about. Optional. It shapes how they speak, never what a document says."
        className={field} />
      <p className={`text-xs ${theme.textMuted}`}>
        {fmt(backstory.length)} / {fmt(limits.backstory ?? BACKSTORY_MAX)} characters
      </p>
      <label htmlFor="assistant-rules" className={`text-xs font-bold ${theme.textSecondary}`}>Rules</label>
      <textarea id="assistant-rules" aria-label="Rules" rows={10} maxLength={limits.text} value={text}
        onChange={(e) => { setText(e.target.value); setWarnings([]); }}
        placeholder="- Answer warmly and briefly.&#10;- Say when you don't know."
        className={`font-mono ${field}`} />
      <p className={`text-xs ${theme.textMuted}`}>
        {fmt(text.length)} / {fmt(limits.text)} characters
        {withWhat ? ` · ${fmt(combined)} with ${withWhat}` : ''} · ≈ {fmt(Math.ceil(combined / 4))} tokens, sent with
        every message, so shorter is faster.
      </p>
      <p className={`text-xs ${theme.textMuted}`}>
        Placeholders, filled in on every message: <code>{'{{date}}'}</code> today&apos;s date,{' '}
        <code>{'{{app}}'}</code> the app&apos;s name, <code>{'{{profile}}'}</code> this profile&apos;s name. Where you
        use one, the app doesn&apos;t say it again.
      </p>
      {shown.map((w) => <p key={w} className="text-xs text-amber-600">{w}</p>)}
      </>)}
      <div className="flex items-center justify-end gap-3 text-xs">
        <button onClick={cancel} className={link}>Cancel</button>
        <button onClick={() => onSave(builtin ? { name, description } : { name, description, backstory, text })}
          disabled={busy || !dirty || !name.trim() || (!builtin && !text.trim())} className={`font-bold ${link}`}>
          Save profile
        </button>
      </div>
      {!builtin && (<>
      <TryProfile theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast} models={models}
        name={name} backstory={backstory} text={text} />
      <button onClick={() => setPreviewOpen((o) => !o)} aria-expanded={previewOpen}
        className={`min-h-11 md:min-h-0 self-start text-xs ${theme.textMuted}`}>
        <span aria-hidden="true">{previewOpen ? '▾' : '▸'}</span> <span>What the model receives</span>
      </button>
      {previewOpen && (
        <pre className={`text-xs whitespace-pre-wrap p-2 rounded border ${theme.border} ${theme.bgSecondary}`}>
          {preview ?? 'Loading…'}
        </pre>
      )}
      </>)}
    </div>
  );
}
