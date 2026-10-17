import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { KOKORO_VOICES } from '../../constants';

/**
 * Admin console: deployment defaults for user preferences
 * (server/services/user_defaults.py). A default is what a setting shows for
 * someone who hasn't chosen it; their own choice stays, unless the admin
 * applies the default to everyone, which replaces it the next time each
 * person opens the app.
 */

const LABELS = {
  chatAutoTts: 'Read replies aloud automatically',
  chatTtsMode: 'When a reply is read aloud',
  think: 'Thinking',
  selectedVoice: 'Voice',
  playbackSpeed: 'Reading speed',
};
const TTS_MODE_LABEL = { streaming: 'While it streams', 'after-complete': 'Once it is complete' };
const THINK_LABEL = { off: 'Off', on: 'On', low: 'Low', medium: 'Medium', high: 'High' };
const ORDER = ['chatAutoTts', 'chatTtsMode', 'think', 'selectedVoice', 'playbackSpeed'];

function shown(name, value) {
  if (name === 'chatAutoTts') return value ? 'on' : 'off';
  if (name === 'chatTtsMode') return TTS_MODE_LABEL[value] ?? value;
  if (name === 'think') return THINK_LABEL[value]?.toLowerCase() ?? value;
  if (name === 'selectedVoice') return KOKORO_VOICES.find((v) => v.id === value)?.name ?? value;
  if (name === 'playbackSpeed') return `${value}×`;
  return String(value);
}

export function DefaultsSection({ theme, apiHost, apiPort, showToast }) {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(null);   // setting name awaiting "Replace for everyone"

  const apply = (view) => { setData(view); setDraft(view.values); };

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/defaults');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      apply(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  const call = async (path, init, done) => {
    setBusy(true);
    try {
      const res = await apiFetch(apiHost, apiPort, path, init);
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
        throw new Error(detail);
      }
      apply(await res.json());
      showToast?.(done, 4000);
    } catch (e) {
      showToast?.(`That didn't work: ${e.message}`, 5000);
    } finally {
      setBusy(false);
    }
  };

  const save = () => call('/v1/admin/defaults', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values: draft }),
  }, 'Defaults saved: people who haven\'t chosen get them the next time they open the app.');

  const applyToEveryone = (name) => {
    setConfirming(null);
    return call(`/v1/admin/defaults/${encodeURIComponent(name)}/apply-to-everyone`, { method: 'POST' },
      `${LABELS[name]}: everyone gets the default the next time they open the app.`);
  };

  const set = (name, value) => setDraft((d) => ({ ...d, [name]: value }));
  const dirty = data && draft && ORDER.some((n) => draft[n] !== data.values[n]);
  const control = `text-xs p-1 rounded border ${theme.border} ${theme.bg} ${theme.text}`;

  const field = (name) => {
    const v = draft[name];
    if (name === 'chatAutoTts') {
      return <input type="checkbox" aria-label={LABELS[name]} checked={!!v} onChange={(e) => set(name, e.target.checked)} />;
    }
    if (name === 'chatTtsMode' || name === 'think') {
      const labels = name === 'think' ? THINK_LABEL : TTS_MODE_LABEL;
      return (
        <select aria-label={LABELS[name]} value={v} onChange={(e) => set(name, e.target.value)} className={control}>
          {(data.choices[name] || Object.keys(labels)).map((c) => <option key={c} value={c}>{labels[c] ?? c}</option>)}
        </select>
      );
    }
    if (name === 'selectedVoice') {
      return (
        <select aria-label={LABELS[name]} value={v} onChange={(e) => set(name, e.target.value)} className={control}>
          {KOKORO_VOICES.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
        </select>
      );
    }
    return (
      <input type="number" aria-label={LABELS[name]} min={0.5} max={2} step={0.1} value={v}
        onChange={(e) => set(name, Math.min(2, Math.max(0.5, parseFloat(e.target.value) || 1)))}
        className={`${control} w-20`} />
    );
  };

  return (
    <section className="flex flex-col gap-3" aria-label="Defaults">
      <h2 className="text-xs font-bold uppercase tracking-wider text-blue-500">Defaults</h2>
      {failed ? (
        <p className="text-xs text-red-500">Couldn&apos;t load the defaults.</p>
      ) : data === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className={`text-xs ${theme.textSecondary}`}>
            What each setting shows for someone who hasn&apos;t chosen it. People keep their own
            choice, unless you apply a default to everyone.
          </p>
          <ul className="flex flex-col gap-2">
            {ORDER.map((name) => (
              <li key={name} className={`flex flex-wrap items-center justify-between gap-2 py-1 border-b ${theme.border}`}>
                <span className="flex flex-col min-w-0">
                  <span className={`text-xs ${theme.text}`}>{LABELS[name]}</span>
                  {data.overridden.includes(name) && (
                    <span className={`text-[10px] ${theme.textMuted}`}>built-in: {shown(name, data.builtIn[name])}</span>
                  )}
                </span>
                <span className="flex items-center gap-3">
                  {field(name)}
                  {confirming === name ? (
                    <span className="flex items-center gap-2 text-[10px]">
                      <span className={theme.textSecondary}>Replaces everyone&apos;s own choice with {shown(name, data.values[name])}.</span>
                      <button onClick={() => applyToEveryone(name)} disabled={busy}
                        className="underline text-red-500 font-bold">Replace for everyone</button>
                      <button onClick={() => setConfirming(null)} className="underline">Cancel</button>
                    </span>
                  ) : (
                    <button onClick={() => setConfirming(name)} disabled={busy || dirty}
                      title={dirty ? 'Save first: this applies the saved default' : undefined}
                      aria-label={`Apply ${LABELS[name].toLowerCase()} to everyone`}
                      className="text-[10px] underline text-blue-500 disabled:opacity-40 disabled:no-underline">
                      Apply to everyone
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <div className="flex justify-end">
            <button onClick={save} disabled={busy || !dirty}
              className="text-xs underline text-blue-500 font-bold disabled:opacity-40 disabled:no-underline">
              Save
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
