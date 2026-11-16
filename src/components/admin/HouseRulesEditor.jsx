import { useEffect, useState } from 'react';

const fmt = (n) => n.toLocaleString('en-US');

/**
 * The admin's house rules (profile library spec): one text that leads every chat, before the chat's profile. Empty
 * removes them. `onSave(text)` resolves to true when the server took it.
 */
export function HouseRulesEditor({ theme, saved, maxChars, busy, onSave, onDirtyChange }) {
  // The parent keys this on `saved`, so a save starts the editor again from what the server kept.
  const [draft, setDraft] = useState(saved);
  const dirty = draft !== saved;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-xs font-bold">House rules</h3>
      <p className={`text-xs ${theme.textSecondary}`}>
        Applies to every chat, before the profile. Put here what every profile needs: what the assistant may and may
        not do, and how it treats people.
      </p>
      <textarea
        aria-label="House rules"
        rows={5}
        maxLength={maxChars}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Never give legal or medical advice; say who to ask instead."
        className={`min-h-11 md:min-h-0 w-full font-mono text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`}
      />
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className={theme.textMuted}>
          {fmt(draft.length)} / {fmt(maxChars)} characters · ≈ {fmt(Math.ceil(draft.length / 4))} tokens
        </span>
        <button
          onClick={() => onSave(draft)}
          disabled={busy || !dirty}
          className="min-h-11 md:min-h-0 underline text-blue-500 font-bold disabled:opacity-40 disabled:no-underline"
        >
          Save house rules
        </button>
      </div>
    </div>
  );
}
