import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Admin console: the assistant profile (v2.4 Task A2) — the assistant's
 * name, personality, tone and house rules. It leads every chat's system
 * message; the app's own rules about documents and tools follow it and win
 * where they conflict. The server cleans, caps and warns; this only edits.
 */

const SOURCE_LABEL = { admin: 'Set here', file: 'From the deployment file', none: 'None' };

const fmt = (n) => n.toLocaleString('en-US');

export function AssistantProfileSection({ theme, apiHost, apiPort, showToast }) {
  const [data, setData] = useState(null);       // the server's view; null while loading
  const [draft, setDraft] = useState('');
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);

  const apply = (view) => { setData(view); setDraft(view.text || ''); };

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
          <textarea
            aria-label="Assistant profile"
            rows={12}
            maxLength={max}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="You are Ada, the reading-room assistant. Answer warmly and briefly."
            className={`w-full font-mono text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`}
          />
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className={theme.textMuted}>
              {fmt(draft.length)} / {fmt(max)} characters · ≈ {fmt(Math.ceil(draft.length / 4))} tokens
            </span>
            <div className="flex gap-3">
              <button
                onClick={() => save('')}
                disabled={saving || data.source !== 'admin'}
                className="underline text-blue-500 disabled:opacity-40 disabled:no-underline"
              >
                Reset to deployment default
              </button>
              <button
                onClick={() => save(draft)}
                disabled={saving || draft === (data.text || '')}
                className="underline text-blue-500 font-bold disabled:opacity-40 disabled:no-underline"
              >
                Save
              </button>
            </div>
          </div>
          {data.warnings?.map((w) => (
            <p key={w} className="text-xs text-amber-600">{w}</p>
          ))}
          <details className="text-xs">
            <summary className={`cursor-pointer ${theme.textMuted}`}>What the model receives</summary>
            <pre className={`mt-2 whitespace-pre-wrap p-2 rounded border ${theme.border} ${theme.bgSecondary}`}>
              {data.preview}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
