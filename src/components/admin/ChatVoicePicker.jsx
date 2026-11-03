import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { SearchableSelect } from '../SearchableList';

/**
 * Admin console: who speaks the chat's replies and Talk (spec 2026-11-03-google-speech). Kokoro reads with each
 * person's own voice; Google Gemini-TTS with one voice for everyone, chosen here. Documents and audiobooks keep
 * Kokoro whatever this says. Google shows only once the deployer has set GOOGLE_TTS_MODEL.
 */

const URL_ = '/v1/admin/chat-voice';
const SOURCE_LINE = {
  admin: 'Set here.',
  env: 'From the server setting CHAT_VOICE.',
  default: 'The default.',
};

async function reasonOf(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail?.message === 'string') return detail.message;
    if (typeof detail === 'string') return detail;
  } catch { /* not JSON */ }
  return `HTTP ${res.status}`;
}

export function ChatVoicePicker({ theme, apiHost, apiPort, showToast }) {
  const [data, setData] = useState(null);   // the server's view; null while loading or if it failed
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let current = true;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, URL_);
        if (res.ok && current) setData(await res.json());
      } catch { /* the control stays hidden */ }
    })();
    return () => { current = false; };
  }, [apiHost, apiPort]);

  const save = useCallback(async (body, done) => {
    setSaving(true);
    try {
      const res = await apiFetch(apiHost, apiPort, URL_, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        showToast?.(`That didn't work: ${await reasonOf(res)}`, 5000);
        return;
      }
      const view = await res.json();
      setData(view);
      done?.(view);
    } catch (e) {
      showToast?.(`That didn't work: ${e.message}`, 5000);
    } finally {
      setSaving(false);
    }
  }, [apiHost, apiPort, showToast]);

  if (!Array.isArray(data?.engines)) return null;   // not loaded, failed, or a server without it
  const select = `min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`;
  const labelOf = (id) => data.engines.find((e) => e.id === id)?.label ?? id;

  const chooseEngine = (engine) => {
    if (saving || engine === data.engine) return;
    save({ engine }, (view) => showToast?.(view.engine === 'google'
      ? `Chat voice: ${labelOf('google')}, ${view.voice}. Replies and Talk are spoken by Google from the next sentence.`
      : `Chat voice: ${labelOf(view.engine)}.`, 5000));
  };

  return (
    <div className="flex flex-col gap-2 pt-2">
      <div className="flex flex-col gap-1">
        <span className={`text-xs font-bold ${theme.text}`}>Voice for the chat and Talk</span>
        <SearchableSelect
          theme={theme}
          aria-label="Voice for the chat and Talk"
          findLabel="Find an engine"
          value={data.engine}
          onChange={chooseEngine}
          aria-busy={saving}
          options={data.engines.map((e) => ({
            value: e.id, label: e.available ? e.label : `${e.label} (needs ${e.needs})`, disabled: !e.available,
          }))}
          className={select}
        />
      </div>
      {data.engine === 'google' && (
        <div className="flex flex-col gap-1">
          <span className={`text-[10px] ${theme.textSecondary}`}>Google&apos;s voice, for everyone</span>
          <SearchableSelect
            theme={theme}
            aria-label="Google's voice"
            findLabel="Find a voice"
            value={data.voice}
            onChange={(voice) => { if (!saving && voice !== data.voice) save({ engine: 'google', voice }); }}
            aria-busy={saving}
            options={data.voices.map((v) => ({ value: v, label: v }))}
            className={select}
          />
        </div>
      )}
      <p className={`text-[10px] ${theme.textSecondary}`}>
        {data.engine === 'google'
          ? `Each sentence of a reply is sent to Google Cloud to be spoken (${data.model}), in Sinhala, Tamil or `
            + 'the default language, by its letters. Google bills per character. Documents and audiobooks keep Kokoro.'
          : 'Kokoro reads with each person\'s own voice from Settings. Documents and audiobooks keep Kokoro whatever is chosen here.'}
      </p>
      <div className="flex flex-wrap items-center gap-3 text-[10px]">
        <span className={theme.textSecondary}>{SOURCE_LINE[data.source] ?? data.source}</span>
        {data.source === 'admin' && (
          <button
            onClick={() => save({ engine: null })}
            disabled={saving}
            aria-label="Use the server setting for the chat voice"
            className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline"
          >
            Use the server setting
          </button>
        )}
      </div>
    </div>
  );
}
