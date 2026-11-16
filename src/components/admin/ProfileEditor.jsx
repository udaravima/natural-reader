import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

const fmt = (n) => n.toLocaleString('en-US');
const DESCRIBE_MAX = 2000;
const PREVIEW_DELAY_MS = 300;   // after the last keystroke, while the preview is shown

/**
 * One profile of the library, new (`initial.id` null) or saved (profile library spec, "For an admin"): its name,
 * the line people see under it in the picker, and the text the assistant reads after the house rules.
 *
 * Generate (helper spec §7): with a helper model set up, the admin describes the assistant and the helper writes a
 * draft into the text. Nothing is saved until Save profile, and unsaved text is replaced only after a confirm.
 *
 * "What the model receives" asks the server for the full system message with this text, unsaved, and the saved
 * house rules, placeholders filled in; it follows the typing while it is shown, and `refreshKey` (a setting saved
 * elsewhere in the console) fetches it again.
 */
export function ProfileEditor({ theme, apiHost, apiPort, showToast, initial, limits, houseText, helperOn, busy,
  refreshKey = 0, onSave, onCancel, onDirtyChange }) {
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [text, setText] = useState(initial.text);
  const [describeText, setDescribeText] = useState('');
  const [generating, setGenerating] = useState(false);
  const [warnings, setWarnings] = useState([]);      // from the last draft or preview, until the text is edited
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState(null);

  const dirty = name !== initial.name || description !== initial.description || text !== initial.text;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const textRef = useRef(text);
  useEffect(() => { textRef.current = text; }, [text]);

  useEffect(() => {
    if (!previewOpen) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, text }),
        });
        if (!res.ok || cancelled) return;
        const body = await res.json();
        if (cancelled) return;
        setPreview(body.preview);
        setWarnings(body.warnings || []);
      } catch { /* the preview stays as it was */ }
    }, PREVIEW_DELAY_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [previewOpen, name, text, refreshKey, apiHost, apiPort]);

  const generate = async () => {
    if (generating || !describeText.trim()) return;
    setGenerating(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: describeText }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast?.(body?.detail?.message || `Writing a draft failed (HTTP ${res.status}).`, 5000);
        return;
      }
      // The text may have changed while the model wrote: judge "unsaved" now, not when Generate was pressed.
      const now = textRef.current;
      if (now.trim() && now !== initial.text && !window.confirm('Replace the unsaved text in the editor with the draft?')) return;
      setText(body.text || '');
      setWarnings(body.warnings || []);
      showToast?.('Draft placed in the editor. Nothing is saved until you press Save profile.', 5000);
    } catch {
      showToast?.('The server could not be reached.', 5000);
    } finally {
      setGenerating(false);
    }
  };

  const cancel = () => {
    if (dirty && !window.confirm('Discard the unsaved changes to this profile?')) return;
    onCancel();
  };

  const combined = houseText ? houseText.length + (text ? 2 + text.length : 0) : text.length;
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
      </div>
      {helperOn === false && (
        <p className={`text-xs ${theme.textMuted}`}>Choose a helper model in Models to generate this.</p>
      )}
      {helperOn && (
        <div className={`flex flex-col gap-2 p-2 rounded border ${theme.border}`}>
          <label htmlFor="assistant-describe" className={`text-xs font-bold ${theme.textSecondary}`}>
            Describe the assistant
          </label>
          <textarea id="assistant-describe" rows={3} maxLength={DESCRIBE_MAX} value={describeText}
            onChange={(e) => setDescribeText(e.target.value)}
            placeholder="A calm tutor called Ada who explains in plain words and says when she isn't sure."
            className={field} />
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className={theme.textMuted}>
              The helper model writes a draft into the text below. Nothing is saved until you press Save profile.
            </span>
            <button onClick={generate} disabled={generating || !describeText.trim()} className={`shrink-0 font-bold ${link}`}>
              {generating ? 'Writing a draft…' : 'Generate'}
            </button>
          </div>
        </div>
      )}
      <textarea aria-label="Profile text" rows={12} maxLength={limits.text} value={text}
        onChange={(e) => { setText(e.target.value); setWarnings([]); }}
        placeholder="You are Ada, the reading-room assistant. Answer warmly and briefly."
        className={`font-mono ${field}`} />
      <p className={`text-xs ${theme.textMuted}`}>
        {fmt(text.length)} / {fmt(limits.text)} characters
        {houseText ? ` · ${fmt(combined)} with house rules` : ''} · ≈ {fmt(Math.ceil(combined / 4))} tokens.
        Every character is read before every reply, so shorter is faster.
      </p>
      <p className={`text-xs ${theme.textMuted}`}>
        Placeholders, filled in on every message: <code>{'{{date}}'}</code> today&apos;s date,{' '}
        <code>{'{{app}}'}</code> the app&apos;s name, <code>{'{{profile}}'}</code> this profile&apos;s name. Where you
        use one, the app doesn&apos;t say it again.
      </p>
      {shown.map((w) => <p key={w} className="text-xs text-amber-600">{w}</p>)}
      <div className="flex items-center justify-end gap-3 text-xs">
        <button onClick={cancel} className={link}>Cancel</button>
        <button onClick={() => onSave({ name, description, text })}
          disabled={busy || !dirty || !name.trim() || !text.trim()} className={`font-bold ${link}`}>
          Save profile
        </button>
      </div>
      <button onClick={() => setPreviewOpen((o) => !o)} aria-expanded={previewOpen}
        className={`min-h-11 md:min-h-0 self-start text-xs ${theme.textMuted}`}>
        <span aria-hidden="true">{previewOpen ? '▾' : '▸'}</span> <span>What the model receives</span>
      </button>
      {previewOpen && (
        <pre className={`text-xs whitespace-pre-wrap p-2 rounded border ${theme.border} ${theme.bgSecondary}`}>
          {preview ?? 'Loading…'}
        </pre>
      )}
    </div>
  );
}
