import { useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { modelLabel } from './useChatModels';

const DESCRIBE_MAX = 6000;   // characters of notes

/**
 * Generate, for a whole character (character profiles spec): the admin's notes, and the model that writes the draft,
 * the helper by default or any chat model. `onDraft({name, description, backstory, text, warnings})` gets the reply;
 * the editor decides what it replaces. Nothing is saved here.
 */
export function CharacterDraft({ theme, apiHost, apiPort, showToast, models, helperOn, onDraft }) {
  const [notes, setNotes] = useState('');
  const [model, setModel] = useState(null);         // null: the default (the helper, else the first model)
  const [generating, setGenerating] = useState(false);
  const chosen = model ?? (helperOn ? '' : models[0]?.id ?? '');
  const field = `min-h-11 md:min-h-0 w-full text-xs p-2 rounded border ${theme.border} ${theme.bg} ${theme.text}`;
  const link = 'min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline';

  if (!helperOn && models.length === 0) {
    return (
      <p className={`text-xs ${theme.textMuted}`}>
        Choose a helper model in Models, or add a chat model, to generate this.
      </p>
    );
  }

  const generate = async () => {
    if (generating || !notes.trim()) return;
    setGenerating(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/assistant/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(chosen ? { description: notes, model: chosen } : { description: notes }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast?.(body?.detail?.message || `Writing a draft failed (HTTP ${res.status}).`, 5000);
        return;
      }
      onDraft(body);
    } catch {
      showToast?.('The server could not be reached.', 5000);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className={`flex flex-col gap-2 p-2 rounded border ${theme.border}`}>
      <label htmlFor="assistant-describe" className={`text-xs font-bold ${theme.textSecondary}`}>
        Describe the character
      </label>
      <textarea id="assistant-describe" rows={5} maxLength={DESCRIBE_MAX} value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Who they are, where they come from, how they speak, what they care about, and any rules they must keep. Paste a biography if you have one."
        className={field} />
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
        <label className={`flex items-center gap-2 ${theme.textMuted}`}>
          Written by
          <select aria-label="Written by" value={chosen} onChange={(e) => setModel(e.target.value)}
            className={`min-h-11 md:min-h-0 max-w-[16rem] text-xs p-1 rounded border ${theme.border} ${theme.bg} ${theme.text}`}>
            {helperOn && <option value="">Helper model</option>}
            {models.map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
          </select>
        </label>
        <button onClick={generate} disabled={generating || !notes.trim()} className={`shrink-0 font-bold ${link}`}>
          {generating ? 'Writing a draft…' : 'Generate'}
        </button>
      </div>
      <p className={`text-xs ${theme.textMuted}`}>
        It writes the name, the description, the backstory and the rules: half a minute on a small model, longer on a
        big one. Nothing is saved until you press Save profile.
      </p>
    </div>
  );
}
