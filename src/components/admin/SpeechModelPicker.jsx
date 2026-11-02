import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { SearchableList, SearchableSelect } from '../SearchableList';

/**
 * Admin console: which speech model voice input runs (spec 2026-11-02-speech-model-choice). One model for the whole
 * deployment. The list comes from the server (speech/catalogue.py): the built-in models, a deployer's
 * SPEECH_MODELS_FILE, and models added here. A change applies from the next recording on every worker. A remote
 * speech engine has no list, so nothing shows then.
 */

const LIST = '/v1/admin/speech-models';
const SOURCE_LINE = {
  admin: 'Set here.',
  env: 'From the server setting SPEECH_MODEL.',
  default: 'The default.',
};
const HOW_IT_RUNS = {
  'faster-whisper': 'Downloads once, the first time it runs.',
  'converted-whisper': 'Downloads once and is converted on this server the first time it runs.',
  'transformers-whisper': 'Downloads once. Runs without converting, so it is slower and uses more memory.',
};
const EMPTY = { repo: '', revision: '', languages: '', label: '', unconverted: false };
const COMMERCIAL = { yes: 'Commercial use: yes.', no: 'Commercial use: no.', check: 'Commercial use: check the licence.' };

const optionText = (m) => [m.label, m.language_names.join(', ')].filter(Boolean).join(' · ');

async function reasonOf(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail?.message === 'string') return detail.message;
    if (typeof detail === 'string') return detail;
  } catch { /* not JSON */ }
  return `HTTP ${res.status}`;
}

export function SpeechModelPicker({ theme, apiHost, apiPort, showToast, voiceOn }) {
  const [data, setData] = useState(null);   // { engine, chosen, source, models }; null while loading or if it failed
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [refused, setRefused] = useState('');

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, LIST);
      if (res.ok) setData(await res.json());
    } catch { /* the control stays hidden */ }
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  // One way to change anything here. Returns the reason on a refusal, so the add form can keep what was typed.
  const send = async (path, options) => {
    setSaving(true);
    try {
      const res = await apiFetch(apiHost, apiPort, path, options);
      if (!res.ok) return await reasonOf(res);
      const view = await res.json();
      setData(view);
      return view;
    } catch (e) {
      return e.message;
    } finally {
      setSaving(false);
    }
  };

  const choose = async (id) => {
    if (saving || id === data.chosen) return;
    const before = data;
    setData({ ...data, chosen: id, source: 'admin' });   // the list shows the choice at once; a refusal puts it back
    const out = await send(`${LIST}/choice`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }),
    });
    if (typeof out === 'string') {
      setData(before);
      showToast?.(`That didn't work: ${out}`, 5000);
      return;
    }
    const label = out.models.find((m) => m.id === id)?.label ?? id;
    showToast?.(voiceOn
      ? `Speech model: ${label}. Loading it now: Admin → Overview shows when it's ready.`
      : `Speech model: ${label}. It loads when Voice input is on.`, 5000);
  };

  const reset = async () => {
    const out = await send(`${LIST}/choice`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: null }),
    });
    if (typeof out === 'string') showToast?.(`That didn't work: ${out}`, 5000);
  };

  const add = async (event) => {
    event.preventDefault();
    if (saving || !form.repo.trim()) return;
    setRefused('');
    const out = await send(LIST, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo: form.repo.trim(),
        revision: form.revision.trim() || null,
        languages: form.languages.split(/[\s,]+/).filter(Boolean),
        label: form.label.trim() || null,
        unconverted: form.unconverted,
      }),
    });
    if (typeof out === 'string') {
      setRefused(out);
      return;
    }
    showToast?.(`Added ${form.label.trim() || form.repo.trim()}. Choose it above to use it.`, 5000);
    setForm(EMPTY);
  };

  const remove = async (model) => {
    const out = await send(`${LIST}/${encodeURIComponent(model.id)}`, { method: 'DELETE' });
    showToast?.(typeof out === 'string' ? `That didn't work: ${out}` : `Removed ${model.label}.`, 5000);
  };

  if (data?.engine !== 'local') return null;
  const current = data.models.find((m) => m.id === data.chosen);
  const blocked = data.models.find((m) => m.why_not);
  const added = data.models.filter((m) => m.removable);
  const label = `flex flex-col gap-1 text-[10px] ${theme.textSecondary}`;
  const input = `min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`;

  return (
    <div className="flex flex-col gap-2 pt-2">
      <div className="flex flex-col gap-1">
        <span className={`text-xs font-bold ${theme.text}`}>Speech model</span>
        <SearchableSelect
          theme={theme}
          aria-label="Speech model"
          findLabel="Find a speech model"
          value={data.chosen}
          onChange={choose}
          aria-busy={saving}
          options={data.models.map((m) => ({ value: m.id, label: optionText(m), disabled: Boolean(m.why_not) }))}
          className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`}
        />
      </div>
      {current && (
        <p className={`text-[10px] ${theme.textSecondary}`}>
          {[HOW_IT_RUNS[current.engine], current.note,
            current.license && `Licence: ${current.license}.`, COMMERCIAL[current.commercial]].filter(Boolean).join(' ')}
        </p>
      )}
      {blocked && <p className="text-[10px] text-amber-600">Some models can&apos;t run here. {blocked.why_not}</p>}
      <div className="flex flex-wrap items-center gap-3 text-[10px]">
        <span className={theme.textSecondary}>{SOURCE_LINE[data.source] ?? data.source}</span>
        {data.source === 'admin' && (
          <button
            onClick={reset}
            disabled={saving}
            aria-label="Use the server setting for the speech model"
            className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline"
          >
            Use the server setting
          </button>
        )}
      </div>
      {added.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className={`text-[10px] ${theme.textSecondary}`}>Added here</span>
          <SearchableList theme={theme} label="Find an added model" items={added} disabled={saving}
            textOf={(m) => `${m.label} ${m.repo}`}>
            {(shown) => shown.map((m) => (
              <div key={m.id} className="flex items-center justify-between gap-2 min-w-0 text-[10px]">
                <span className={`min-w-0 truncate ${theme.text}`} title={m.repo}>{m.label}</span>
                <button
                  onClick={() => remove(m)}
                  disabled={saving}
                  aria-label={`Remove ${m.label}`}
                  className="min-h-11 md:min-h-0 underline text-red-500 disabled:opacity-40 disabled:no-underline"
                >
                  Remove
                </button>
              </div>
            ))}
          </SearchableList>
        </div>
      )}
      <details className="text-xs">
        <summary className={`min-h-11 md:min-h-0 flex items-center cursor-pointer text-[10px] underline text-blue-500`}>
          Add a model from Hugging Face
        </summary>
        <form onSubmit={add} className="flex flex-col gap-2 pt-2">
          <label className={label}>
            Repo
            <input
              value={form.repo} placeholder="owner/name" required spellCheck={false} autoComplete="off"
              onChange={(e) => setForm({ ...form, repo: e.target.value })} className={input}
            />
          </label>
          <label className={label}>
            Revision (optional: empty means the latest, which is then pinned)
            <input
              value={form.revision} spellCheck={false} autoComplete="off"
              onChange={(e) => setForm({ ...form, revision: e.target.value })} className={input}
            />
          </label>
          <label className={label}>
            Language codes (optional, such as si or ta: empty means what the model card says)
            <input
              value={form.languages} spellCheck={false} autoComplete="off"
              onChange={(e) => setForm({ ...form, languages: e.target.value })} className={input}
            />
          </label>
          <label className={label}>
            Name (optional)
            <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} className={input} />
          </label>
          <label className={`min-h-11 md:min-h-0 flex items-center gap-2 text-[10px] ${theme.textSecondary}`}>
            <input
              type="checkbox" checked={form.unconverted}
              onChange={(e) => setForm({ ...form, unconverted: e.target.checked })}
              className="h-3 w-3 accent-blue-500"
            />
            Run it without converting (slower, for comparing)
          </label>
          {refused && <p role="alert" className="text-[10px] text-red-500">{refused}</p>}
          <button
            type="submit"
            disabled={saving || !form.repo.trim()}
            className="self-start min-h-11 md:min-h-0 px-3 py-1 text-xs rounded bg-blue-600 text-white disabled:opacity-40"
          >
            {saving ? 'Checking…' : 'Add model'}
          </button>
        </form>
      </details>
    </div>
  );
}
