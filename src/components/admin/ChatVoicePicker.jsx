import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { SearchableSelect } from '../SearchableList';

/**
 * Admin console: who speaks the chat's replies and Talk (spec 2026-11-03-google-speech). Kokoro reads with each
 * person's own voice; Google Gemini-TTS or Chirp 3 HD (spec 2026-11-18-chirp-voice) with the voice chosen here,
 * unless a person picks their own. Documents and audiobooks keep Kokoro whatever this says. Gemini-TTS shows only
 * once the deployer has set GOOGLE_TTS_MODEL; Chirp once Google is set up. Chirp has no Sinhala voice: Gemini-TTS
 * reads those sentences when it is set up.
 */

const GOOGLE_ENGINES = ['google', 'chirp'];

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

function helpLine(data) {
  const after = ' Google bills per character. Documents and audiobooks keep Kokoro.';
  if (data.engine === 'google') {
    return `Each sentence of a reply is sent to Google Cloud to be spoken (${data.model}), in Sinhala, Tamil or `
      + `the default language, by its letters.${after}`;
  }
  if (data.engine === 'chirp') {
    return 'Each sentence of a reply is sent to Google Cloud to be spoken by Chirp 3 HD, in Tamil or the default '
      + 'language, by its letters. '
      + (data.model
        ? `Chirp has no Sinhala voice: Sinhala sentences are read by Gemini-TTS (${data.model}).`
        : 'Sinhala sentences have no voice, since Chirp has none: set GOOGLE_TTS_MODEL for Gemini-TTS to read them.')
      + after;
  }
  return 'Kokoro reads with each person\'s own voice from Settings. Documents and audiobooks keep Kokoro whatever is chosen here.';
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
    save({ engine }, (view) => showToast?.(GOOGLE_ENGINES.includes(view.engine)
      ? `Chat voice: ${labelOf(view.engine)}, ${view.voice}. Replies and Talk are spoken by Google from the next sentence.`
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
      {GOOGLE_ENGINES.includes(data.engine) && (
        <div className="flex flex-col gap-1">
          <span className={`text-[10px] ${theme.textSecondary}`}>Google&apos;s voice, for everyone</span>
          <SearchableSelect
            theme={theme}
            aria-label="Google's voice"
            findLabel="Find a voice"
            value={data.voice}
            onChange={(voice) => { if (!saving && voice !== data.voice) save({ engine: data.engine, voice }); }}
            aria-busy={saving}
            options={data.voices.map((v) => ({ value: v, label: v }))}
            className={select}
          />
          {Array.isArray(data.accents) && data.accents.length > 0 && (
            <>
              <span className={`text-[10px] ${theme.textSecondary}`}>English accent</span>
              <SearchableSelect
                theme={theme}
                aria-label="English accent"
                findLabel="Find an accent"
                value={data.accent}
                onChange={(accent) => {
                  if (!saving && accent !== data.accent) save({ engine: data.engine, voice: data.voice, accent });
                }}
                aria-busy={saving}
                options={data.accents.map((a) => ({ value: a.code, label: a.label }))}
                className={select}
              />
            </>
          )}
        </div>
      )}
      <p className={`text-[10px] ${theme.textSecondary}`}>
        {helpLine(data)}
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
