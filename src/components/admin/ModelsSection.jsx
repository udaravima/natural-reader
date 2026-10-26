import { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { useSectionSettings } from '../../hooks/useSectionSettings';
import { SettingRow, SectionSaveBar } from './SettingRow';

/**
 * Admin console: Models (spec §4.2.1), what the server does with the models it can reach: which model reads
 * documents, how much context a local Ollama request asks for, what a model's window is when the provider
 * doesn't say, whether a provider counts as local or cloud, and which chat models people are offered.
 * The console can only narrow: the chat models are intersected with the server's allow-lists, and an empty
 * selection means "the server's list", never "everything".
 *
 * The settings come from useSectionSettings; the catalog the rows choose from (providers and models) comes from
 * GET /v1/admin/models, which fails on its own: the settings rows then still render.
 * A map or list setting is drafted whole (s.set(name, newMap)), so the drafts always hold what the server accepts.
 */

const DEFAULT_LOCAL_NUM_CTX = 16384;   // tokens; the server's DEFAULT_LOCAL_NUM_CTX
const SIZE_RANGE = [2048, 2097152];
const WINDOW_RANGE = [1024, 10000000];
const SIZE_ERROR = 'Must be a whole number of tokens from 2,048 to 2,097,152.';
const WINDOW_ERROR = 'Must be a whole number of tokens from 1,024 to 10,000,000.';
const CHAT_READER = 'The chat model of each conversation';
const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';
const fmt = (n) => n.toLocaleString('en-US');

const LIMITS = {
  context_size: { range: SIZE_RANGE, error: SIZE_ERROR },
  context_sizes: { range: SIZE_RANGE, error: SIZE_ERROR },
  context_windows: { range: WINDOW_RANGE, error: WINDOW_ERROR },
};
const validNumber = (name, text) => {
  const [low, high] = LIMITS[name].range;
  return /^\d+$/.test(text) && Number(text) >= low && Number(text) <= high;
};

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The host as the catalog prints it may carry a port ("ollama:11434"); the ollama.com test is on the name alone.
const hostName = (host) => String(host ?? '').toLowerCase().replace(/:\d+$/, '');

// Whether a request to this model must name its own context size, so it gets a per-model size here. Mirrors
// `sizes_context` in server/services/model_settings.py: an Ollama model that isn't Ollama cloud, on a host that
// isn't ollama.com or a subdomain. The Local/Cloud mark doesn't enter into it: it only decides what counts as
// cloud usage (ruling P11).
function sizesContext(model, provider) {
  if (model.kind !== 'ollama' || model.cloud_name) return false;
  const host = hostName(provider?.host);
  return !(host === 'ollama.com' || host.endsWith('.ollama.com'));
}

export function ModelsSection({ theme, apiHost, apiPort, showToast, onDirtyChange }) {
  const s = useSectionSettings(apiHost, apiPort, 'models');
  // The catalog is its own request: { status: 'loading' | 'ready' | 'failed', providers, models, limits }.
  const [catalog, setCatalog] = useState({ status: 'loading', providers: [], models: [], limits: {} });
  // What is typed in a number box, while it is typed: a half-typed or invalid number can't live in the settings
  // drafts. Keyed by setting (and model); a missing key = show the setting's own value.
  const [typed, setTyped] = useState({});

  useEffect(() => { onDirtyChange?.(s.dirty); }, [s.dirty, onDirtyChange]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/models');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        if (!cancelled) {
          setCatalog({ status: 'ready', providers: body.providers ?? [], models: body.models ?? [], limits: body.limits ?? {} });
        }
      } catch {
        if (!cancelled) setCatalog({ status: 'failed', providers: [], models: [], limits: {} });
      }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  const { providers, models } = catalog;
  const ceiling = catalog.limits.num_ctx_max;   // tokens, or null/undefined for no limit
  const ceilingEnv = s.info.find((r) => r.id === 'num_ctx_max')?.env ?? 'INFERENCE_NUM_CTX_MAX';
  const usedDefault = ceiling ? Math.min(DEFAULT_LOCAL_NUM_CTX, ceiling) : DEFAULT_LOCAL_NUM_CTX;

  const locality = s.value('provider_locality') ?? {};
  const sizeMap = s.value('context_sizes') ?? {};
  const windowMap = s.value('context_windows') ?? {};
  const chatModels = s.value('chat_models') ?? [];
  const reader = s.value('reader') ?? 'chat';
  const providerByName = Object.fromEntries(providers.map((p) => [p.name, p]));
  const sizedModels = models.filter((m) => sizesContext(m, providerByName[m.provider]));
  const unreportedModels = models.filter((m) => m.reported_window == null);
  const readerIds = [...new Set([...models.map((m) => m.id), reader].filter((id) => id !== 'chat'))];

  const typedKey = (name, id = '') => `${name}\n${id}`;
  const typedFor = (name, id) => typed[typedKey(name, id)];
  const invalid = (name, id) => {
    const text = typedFor(name, id);
    return text !== undefined && text !== '' && !validNumber(name, text);
  };
  // Only a box that is on screen can hold Save off: its error line is the reason. A model that leaves the list
  // (its provider stops answering, so the catalog drops it) keeps what was typed, and counts again if it comes back.
  const anyInvalid = invalid('context_size')
    || sizedModels.some((m) => invalid('context_sizes', m.id))
    || unreportedModels.some((m) => invalid('context_windows', m.id));
  const canSave = !anyInvalid;

  // A map or list setting is drafted whole. One that ends up the same as the setting without the console's
  // own choice goes back to following the server, so Save never writes an empty override.
  const draft = (name, next) => (same(next, s.fallback(name)) ? s.useServer(name) : s.set(name, next));

  const typeScalar = (name, text) => {
    setTyped((t) => ({ ...t, [typedKey(name)]: text }));
    if (text === '') s.useServer(name);
    else if (validNumber(name, text)) s.set(name, Number(text));
  };
  const typeInMap = (name, map, id, text) => {
    setTyped((t) => ({ ...t, [typedKey(name, id)]: text }));
    if (text !== '' && !validNumber(name, text)) return;
    const next = { ...map };
    if (text === '') delete next[id]; else next[id] = Number(text);
    draft(name, next);
  };
  const setLocality = (provider, value) => {
    const next = { ...locality };
    if (value === 'auto') delete next[provider]; else next[provider] = value;
    draft('provider_locality', next);
  };
  const toggleChatModel = (id) => {
    const on = chatModels.includes(id);
    // Ids the catalog doesn't list (a provider that is down) stay as they are; the catalog order is kept for the rest.
    const kept = chatModels.filter((m) => !models.some((x) => x.id === m));
    draft('chat_models', [...kept, ...models.map((m) => m.id).filter((m) => (m === id ? !on : chatModels.includes(m)))]);
  };

  const clearTyped = (name) => setTyped((t) => Object.fromEntries(Object.entries(t).filter(([k]) => k.split('\n')[0] !== name)));
  const resetRow = (name) => { clearTyped(name); s.useServer(name); };
  const discard = () => { setTyped({}); s.discard(); };
  const save = async () => {
    if (!(await s.save())) return;
    setTyped({});
    showToast?.('Model settings saved. They apply from the next message.', 4000);
  };

  const row = (name, id, label, extra = {}) => ({
    theme, id, label, source: s.source(name), envName: s.envName(name),
    canUseServer: s.canUseServer(name), onUseServer: () => resetRow(name), ...extra,
  });
  const describedBy = (id, ...more) => [`${id}-help`, `${id}-source`, ...more].filter(Boolean).join(' ');
  const inputClass = `w-32 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-h-11 md:min-h-0`;
  const selectClass = `px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-h-11 md:min-h-0`;
  const optionClass = `flex items-center gap-2 text-xs ${theme.text} min-h-11 md:min-h-0`;
  const box = 'h-3 w-3 accent-blue-500';
  const modelLabel = `text-xs break-all ${theme.text}`;

  // One number box per model, for the two settings that are a model -> tokens map.
  const mapInputs = (name, group, list, map) => list.map((m, i) => {
    const id = `${group}-${i}`;
    const text = typedFor(name, m.id);
    const bad = invalid(name, m.id);
    return (
      <div key={m.id} className="flex flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={id} className={modelLabel}>{m.id}</label>
          <input
            id={id} type="number" step={1} inputMode="numeric" disabled={s.saving}
            value={text ?? map[m.id] ?? ''}
            onChange={(e) => typeInMap(name, map, m.id, e.target.value)}
            aria-invalid={bad || undefined}
            aria-describedby={describedBy(group, bad && `${id}-error`)}
            className={inputClass}
          />
        </div>
        {bad && <p id={`${id}-error`} className="text-xs text-red-500">{LIMITS[name].error}</p>}
      </div>
    );
  });

  const sizeText = typedFor('context_size');
  const sizeBad = invalid('context_size');
  const catalogNote = catalog.status === 'loading'
    ? <p className={`text-xs ${theme.textMuted}`}>Loading the models…</p>
    : catalog.status === 'failed' && <p className="text-xs text-red-500">Couldn&apos;t load the models.</p>;

  return (
    <section className="flex flex-col gap-4" aria-label="Models">
      <h2 className={sectionTitle}>Models</h2>
      {s.status === 'loading' && <p className={`text-xs ${theme.textMuted}`}>Loading…</p>}
      {s.status === 'failed' && <p className="text-xs text-red-500">Couldn&apos;t load the model settings.</p>}
      {s.status === 'ready' && (
        <>
          {catalogNote}
          <div className={`flex flex-col gap-3 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
            <SettingRow {...row('reader', 'models-reader', 'Reader', {
              help: 'The model that reads documents and web pages for the chat model.',
            })}>
              <select
                id="models-reader" value={reader} disabled={s.saving}
                onChange={(e) => s.set('reader', e.target.value)}
                aria-describedby={describedBy('models-reader')} className={selectClass}
              >
                <option value="chat">{CHAT_READER}</option>
                {readerIds.map((id) => <option key={id} value={id}>{id}</option>)}
              </select>
            </SettingRow>

            <SettingRow {...row('context_size', 'models-context-size', 'Context size for local Ollama models', {
              help: `One size for every request to a local Ollama model, so it isn't reloaded. Empty: each person's own setting, else ${fmt(usedDefault)}.`,
            })}>
              <input
                id="models-context-size" type="number" step={1} inputMode="numeric" disabled={s.saving}
                value={sizeText ?? s.value('context_size') ?? ''}
                onChange={(e) => typeScalar('context_size', e.target.value)}
                aria-invalid={sizeBad || undefined}
                aria-describedby={describedBy('models-context-size', ceiling && 'models-context-size-limit', sizeBad && 'models-context-size-error')}
                className={inputClass}
              />
              {ceiling > 0 && (
                <p id="models-context-size-limit" className="text-[10px] text-amber-600">
                  At most {fmt(ceiling)} (<code>{ceilingEnv}</code>).
                </p>
              )}
              {sizeBad && <p id="models-context-size-error" className="text-xs text-red-500">{SIZE_ERROR}</p>}
            </SettingRow>

            <SettingRow {...row('context_sizes', 'models-context-sizes', 'Per-model sizes', {
              help: 'Overrides the size above for one model. Empty: the size above.', group: true,
            })}>
              <div role="group" aria-labelledby="models-context-sizes-label"
                aria-describedby={describedBy('models-context-sizes')} className="flex flex-col gap-1">
                {mapInputs('context_sizes', 'models-context-sizes', sizedModels, sizeMap)}
                {catalog.status === 'ready' && sizedModels.length === 0 && (
                  <p className={`text-xs ${theme.textSecondary}`}>No local Ollama models to size.</p>
                )}
              </div>
            </SettingRow>

            <SettingRow {...row('context_windows', 'models-context-windows', 'Declared context windows', {
              help: "What history is trimmed to when the provider doesn't say.", group: true,
            })}>
              <div role="group" aria-labelledby="models-context-windows-label"
                aria-describedby={describedBy('models-context-windows')} className="flex flex-col gap-1">
                {mapInputs('context_windows', 'models-context-windows', unreportedModels, windowMap)}
                {catalog.status === 'ready' && unreportedModels.length === 0 && (
                  <p className={`text-xs ${theme.textSecondary}`}>Every model reports its own window.</p>
                )}
              </div>
            </SettingRow>

            <SettingRow {...row('provider_locality', 'models-provider-locality', 'Providers', {
              help: 'Local or cloud decides what counts as cloud usage.',
              group: true,
            })}>
              <div role="group" aria-labelledby="models-provider-locality-label"
                aria-describedby={describedBy('models-provider-locality')} className="flex flex-col gap-2">
                {providers.map((p, i) => {
                  const id = `models-provider-${i}`;
                  return (
                    <div key={p.name} className="flex flex-col gap-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <label htmlFor={id} className={modelLabel}>{p.name}</label>
                        <select
                          id={id} value={locality[p.name] ?? 'auto'} disabled={s.saving}
                          onChange={(e) => setLocality(p.name, e.target.value)}
                          aria-describedby={[`${id}-detail`, !p.reachable && `${id}-down`].filter(Boolean).join(' ')}
                          className={selectClass}
                        >
                          <option value="auto">Auto ({p.detected})</option>
                          <option value="local">Local</option>
                          <option value="cloud">Cloud</option>
                        </select>
                      </div>
                      <span id={`${id}-detail`} className={`text-[10px] ${theme.textSecondary}`}>{p.kind} · {p.host}</span>
                      {!p.reachable && <span id={`${id}-down`} className="text-[10px] text-amber-600">Can&apos;t be reached.</span>}
                    </div>
                  );
                })}
              </div>
            </SettingRow>

            <SettingRow {...row('chat_models', 'models-chat-models', 'Chat models offered', {
              help: 'People can chat only with the ticked models. None ticked: every model the server allows.',
              group: true,
            })}>
              <div role="group" aria-labelledby="models-chat-models-label"
                aria-describedby={describedBy('models-chat-models', catalog.status === 'ready' && chatModels.length === 0 && 'models-chat-models-none')}
                className="flex flex-col gap-1">
                {models.map((m) => (
                  <label key={m.id} className={`${optionClass} ${s.saving ? 'opacity-60' : 'cursor-pointer'}`}>
                    <input
                      type="checkbox" checked={chatModels.includes(m.id)} disabled={s.saving}
                      onChange={() => toggleChatModel(m.id)} className={box}
                    />
                    <span className="break-all">{m.id}</span>
                  </label>
                ))}
                {catalog.status === 'ready' && chatModels.length === 0 && (
                  <p id="models-chat-models-none" className={`text-xs ${theme.textSecondary}`}>Every model the server allows.</p>
                )}
              </div>
            </SettingRow>

            <SectionSaveBar theme={theme} dirty={s.dirty} saving={s.saving} error={s.error}
              canSave={canSave} onSave={save} onDiscard={discard} />
          </div>
        </>
      )}
    </section>
  );
}
