import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Admin console: the assistant profile (v2.4 Task A2) — the assistant's
 * name, personality, tone and house rules. It leads every chat's system
 * message; the app's own rules about documents and tools follow it and win
 * where they conflict. The server cleans, caps and warns; this only edits.
 *
 * Generate (helper spec §7): with a helper model set up, the admin describes the assistant and the helper writes a
 * draft into the editor. Nothing is saved until Save, and unsaved text is replaced only after a confirm.
 */

const SOURCE_LABEL = { admin: 'Set here', file: 'From the deployment file', none: 'None' };

const fmt = (n) => n.toLocaleString('en-US');
const DESCRIPTION_MAX = 2000;

export function AssistantProfileSection({ theme, apiHost, apiPort, showToast, refreshKey = 0, onDirtyChange }) {
  const [data, setData] = useState(null);       // the server's view; null while loading
  const [draft, setDraft] = useState('');
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [helperOn, setHelperOn] = useState(null);   // null until the server says
  const [description, setDescription] = useState('');
  const [generating, setGenerating] = useState(false);
  const [draftWarnings, setDraftWarnings] = useState([]);   // about a generated draft, until it is saved or reloaded

  const apply = (view) => { setData(view); setDraft(view.text || ''); setDraftWarnings([]); };

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      apply(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  // Whether a helper model is set up. `refreshKey` changes when a setting was saved elsewhere in the console.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/settings/helper');
        if (!cancelled) setHelperOn(res.ok ? (await res.json()).on === true : false);
      } catch {
        if (!cancelled) setHelperOn(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort, refreshKey]);

  // `refreshKey` is bumped when something the preview depends on changed elsewhere in the console (the web
  // search mode, chat options): fetch again, take only the preview, so what is being typed stays. A failed
  // refresh leaves the old preview as it was.
  useEffect(() => {
    if (!refreshKey) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant');
        if (!res.ok || cancelled) return;
        const view = await res.json();
        if (!cancelled) setData((d) => (d ? { ...d, preview: view.preview } : d));
      } catch { /* the preview stays as it was */ }
    })();
    return () => { cancelled = true; };
  }, [refreshKey, apiHost, apiPort]);

  const save = async (text) => {
    setSaving(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      apply(await res.json());
      showToast?.(text ? 'Assistant profile saved: it applies from the next message.' : 'Assistant profile reset.', 4000);
    } catch (e) {
      showToast?.(`Saving the profile failed: ${e.message}`, 5000);
    } finally {
      setSaving(false);
    }
  };

  // The console asks before a tab switch drops an unsaved draft.
  const dirty = data !== null && draft !== (data.text || '');

  const generate = async () => {
    if (generating || !description.trim()) return;
    setGenerating(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast?.(body?.detail?.message || `Writing a draft failed (HTTP ${res.status}).`, 5000);
        return;
      }
      // The editor may have changed while the model wrote: judge "unsaved" now, not when Generate was pressed.
      if (editorRef.current.dirty && !window.confirm('Replace the unsaved text in the editor with the draft?')) return;
      setDraft(body.text || '');
      setDraftWarnings(body.warnings || []);
      showToast?.('Draft placed in the editor. Nothing is saved until you press Save.', 5000);
    } catch {
      showToast?.('The server could not be reached.', 5000);
    } finally {
      setGenerating(false);
    }
  };
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const editorRef = useRef({ dirty });
  useEffect(() => { editorRef.current = { dirty }; }, [dirty]);

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';
  const max = data?.maxChars ?? 8000;

  return (
    <section className="flex flex-col gap-3" aria-label="Assistant">
      <div className="flex items-center gap-2">
        <h2 className={sectionTitle}>Assistant</h2>
        {data && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${theme.border} ${theme.textMuted}`}>
            {SOURCE_LABEL[data.source] ?? data.source}
          </span>
        )}
      </div>
      {failed ? (
        <p className="text-xs text-red-500">Couldn&apos;t load the assistant profile.</p>
      ) : data === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className={`text-xs ${theme.textSecondary}`}>
            The assistant&apos;s name, personality, tone and house rules. It comes first in every chat;
            the app&apos;s own rules about documents and tools follow it and take precedence. Every
            character is read before every reply, so shorter is faster.
          </p>
          {helperOn === false && (
            <p className={`text-xs ${theme.textMuted}`}>Choose a helper model in Models to generate this.</p>
          )}
          {helperOn && (
            <div className={`flex flex-col gap-2 p-2 rounded border ${theme.border}`}>
              <label htmlFor="assistant-describe" className={`text-xs font-bold ${theme.textSecondary}`}>
                Describe the assistant
              </label>
              <textarea
                id="assistant-describe"
                rows={3}
                maxLength={DESCRIPTION_MAX}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="A calm tutor called Ada who explains in plain words and says when she isn't sure."
                className={`min-h-11 md:min-h-0 w-full text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`}
              />
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className={theme.textMuted}>
                  The helper model writes a draft into the editor below. Nothing is saved until you press Save.
                </span>
                <button
                  onClick={generate}
                  disabled={generating || !description.trim()}
                  className="min-h-11 md:min-h-0 shrink-0 underline text-blue-500 font-bold disabled:opacity-40 disabled:no-underline"
                >
                  {generating ? 'Writing a draft…' : 'Generate'}
                </button>
              </div>
            </div>
          )}
          <textarea
            aria-label="Assistant profile"
            rows={12}
            maxLength={max}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="You are Ada, the reading-room assistant. Answer warmly and briefly."
            className={`min-h-11 md:min-h-0 w-full font-mono text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`}
          />
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className={theme.textMuted}>
              {fmt(draft.length)} / {fmt(max)} characters · ≈ {fmt(Math.ceil(draft.length / 4))} tokens
            </span>
            <div className="flex gap-3">
              <button
                onClick={() => save('')}
                disabled={saving || data.source !== 'admin'}
                className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline"
              >
                Reset to deployment default
              </button>
              <button
                onClick={() => save(draft)}
                disabled={saving || !dirty}
                className="min-h-11 md:min-h-0 underline text-blue-500 font-bold disabled:opacity-40 disabled:no-underline"
              >
                Save
              </button>
            </div>
          </div>
          {[...new Set([...draftWarnings, ...(dirty ? [] : data.warnings || [])])].map((w) => (
            <p key={w} className="text-xs text-amber-600">{w}</p>
          ))}
          <details className="text-xs">
            <summary className={`min-h-11 md:min-h-0 cursor-pointer ${theme.textMuted}`}>What the model receives</summary>
            <pre className={`mt-2 whitespace-pre-wrap p-2 rounded border ${theme.border} ${theme.bgSecondary}`}>
              {data.preview}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
