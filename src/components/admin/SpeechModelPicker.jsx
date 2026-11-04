import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { SearchableList, SearchableSelect } from '../SearchableList';
import { GoogleRecognizers } from './GoogleRecognizers';

/**
 * Admin console: who hears speech (spec 2026-11-03-google-speech) and which speech model voice input runs (spec
 * 2026-11-02-speech-model-choice). One engine and one model for the whole deployment. An engine shows only once the
 * deployer has configured it. Models made for one language can be offered to people, each then hearing its
 * language (spec 2026-11-04-speech-choices). The model list comes from the server (speech/catalogue.py): the built-in models, a
 * deployer's SPEECH_MODELS_FILE, and models added here; a remote engine has no list. A change applies from the next
 * recording on every worker. Google's recognizers are ticked from Google's own list (GoogleRecognizers.jsx, spec
 * 2026-11-04-google-recognizers), shown once Google is set up, and before Google can be chosen.
 */

const LIST = '/v1/admin/speech-models';
const ENGINE = '/v1/admin/speech-engine';
const ENGINE_SOURCE_LINE = {
  admin: 'Set here.',
  env: 'From the server setting SPEECH_ENGINE.',
  default: 'The default.',
};
const ENGINE_LINE = {
  openai: 'Recordings go to the OpenAI-compatible server in SPEECH_URL; SPEECH_MODEL names its model.',
  google: 'Recordings go to Google Cloud, to the recognizers ticked below, whose own settings apply. '
    + 'A recording stops at 55 seconds, as Google takes a minute at most.',
};
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
  // A ticked model downloads in the background: look again every 5 s until each one is on this server.
  const downloading = Boolean(data?.models?.some((m) => m.offered && m.on_disk === false));
  useEffect(() => {
    if (!downloading) return undefined;
    const timer = setTimeout(load, 5000);
    return () => clearTimeout(timer);
  }, [downloading, data, load]);

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

  const chooseEngine = async (engine) => {
    if (saving || engine === data.engine) return;
    const out = await send(ENGINE, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ engine }),
    });
    if (typeof out === 'string') {
      showToast?.(`That didn't work: ${out}`, 5000);
      return;
    }
    const label = out.engines?.find((e) => e.id === out.engine)?.label ?? out.engine;
    showToast?.(`Speech recognition: ${label}, from the next recording.`, 5000);
  };

  const resetEngine = async () => {
    const out = await send(ENGINE, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ engine: null }),
    });
    if (typeof out === 'string') showToast?.(`That didn't work: ${out}`, 5000);
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

  if (!data) return null;
  const engines = data.engines ?? [];
  const engineChoice = engines.length > 0 && (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        <span className={`text-xs font-bold ${theme.text}`}>Speech recognition</span>
        <SearchableSelect
          theme={theme}
          aria-label="Speech recognition"
          findLabel="Find an engine"
          value={data.engine}
          onChange={chooseEngine}
          aria-busy={saving}
          options={engines.map((e) => ({
            value: e.id, label: e.available ? e.label : `${e.label} (needs ${e.needs})`, disabled: !e.available,
          }))}
          className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`}
        />
      </div>
      {ENGINE_LINE[data.engine] && <p className={`text-[10px] ${theme.textSecondary}`}>{ENGINE_LINE[data.engine]}</p>}
      <div className="flex flex-wrap items-center gap-3 text-[10px]">
        <span className={theme.textSecondary}>{ENGINE_SOURCE_LINE[data.engine_source] ?? data.engine_source}</span>
        {data.engine_source === 'admin' && (
          <button
            onClick={resetEngine}
            disabled={saving}
            aria-label="Use the server setting for speech recognition"
            className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline"
          >
            Use the server setting
          </button>
        )}
      </div>
    </div>
  );
  // Google's recognizers: to tick before Google can be chosen, to change once it hears speech, and still there after
  // the first tick makes Google choosable (it must not vanish under the admin's click).
  const googleEngine = engines.find((e) => e.id === 'google');
  const recognizers = data.google?.offered
    && (data.engine === 'google' || googleEngine?.available === false || data.google.source === 'admin') && (
    <GoogleRecognizers theme={theme} apiHost={apiHost} apiPort={apiPort} showToast={showToast} onSaved={setData} />
  );
  if (data.engine !== 'local') return <div className="flex flex-col gap-2 pt-2">{engineChoice}{recognizers}</div>;
  const current = data.models.find((m) => m.id === data.chosen);
  // Offer to people (spec 2026-11-04-speech-choices): a ticked model hears the language it is made for, when a
  // person says they speak it. The list's order decides between two models for one language.
  const offerable = data.models.filter((m) => m.offerable);
  const offer = async (model) => {
    const ids = data.models.filter((m) => (m.id === model.id ? !m.offered : m.offered)).map((m) => m.id);
    const out = await send(`${LIST}/offered`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
    });
    if (typeof out === 'string') showToast?.(`That didn't work: ${out}`, 5000);
  };
  const blocked = data.models.find((m) => m.why_not);
  const added = data.models.filter((m) => m.removable);
  const label = `flex flex-col gap-1 text-[10px] ${theme.textSecondary}`;
  const input = `min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`;

  return (
    <div className="flex flex-col gap-2 pt-2">
      {engineChoice}
      {recognizers}
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
      {offerable.length > 0 && (
        <div role="group" aria-label="Offered to people" className="flex flex-col gap-1">
          <span className={`text-xs font-bold ${theme.text}`}>Offered to people</span>
          <p className={`text-[10px] ${theme.textSecondary}`}>
            People pick the language they speak beside the microphone and in Talk; a ticked model hears its language.
            The model above hears everything else.
          </p>
          {offerable.map((m) => (
            <label key={m.id} className={`min-h-11 md:min-h-0 flex items-center gap-2 text-[10px] ${theme.text}`}>
              <input type="checkbox" checked={Boolean(m.offered)} disabled={saving} onChange={() => offer(m)}
                className="h-3 w-3 accent-blue-500" />
              <span className="min-w-0 flex-1 truncate">{optionText(m)}</span>
              {m.offered && (
                <span className={theme.textSecondary}>
                  {m.on_disk ? 'On this server' : "Downloading: people see it once it's here"}
                </span>
              )}
            </label>
          ))}
        </div>
      )}
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
