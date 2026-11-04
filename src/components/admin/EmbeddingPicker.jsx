import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { SearchableSelect } from '../SearchableList';

/**
 * Admin console: Models → Embeddings (spec §4.2.3–4.2.4). The model that turns passages and questions into
 * vectors for search. Choosing another one is two steps, Check then Use: the server checks the model gives 768
 * numbers (or is trained to be shortened to 768), then re-embeds the library in the background while search keeps
 * using the current model, and switches in one step. Cancel before the switch keeps the current model.
 *
 * Reads GET /v1/admin/embeddings, and again every 5 s while a re-embed runs. The model list is the provider's own
 * (GET /v1/admin/embeddings/models); a provider that lists none takes a typed name.
 */

const POLL_MS = 5000;
const ACTIVE = new Set(['building', 'switching', 'failed']);
const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';
const fmt = (n) => n.toLocaleString('en-US');

async function reasonOf(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail?.message === 'string') return detail.message;
    if (typeof detail === 'string') return detail;
  } catch { /* not JSON */ }
  return `HTTP ${res.status}`;
}

const sendJson = (method, body) => ({
  method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

function progressLine(m) {
  if (m.state === 'failed') {
    return m.model ? `Re-embedding for ${m.model} is paused: ${m.error}` : `The chosen model isn’t used: ${m.error}`;
  }
  if (m.state === 'switching') return `Re-embedding for ${m.model}: every passage done, switching now.`;
  return `Re-embedding for ${m.model}: ${fmt(m.filled)} of ${fmt(m.total)} passages.`;
}

export function EmbeddingPicker({ theme, apiHost, apiPort, showToast }) {
  const [data, setData] = useState(null);           // the server's view; null while loading
  const [failed, setFailed] = useState(false);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [listing, setListing] = useState({ provider: null, names: [] });   // one provider's model names
  const [verdict, setVerdict] = useState(null);     // { for: "provider|model", ok, reason }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/embeddings');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [apiHost, apiPort]);

  const adopt = (view) => {
    setData(view);
    setProvider(view.target.provider);
    setModel(view.target.model);
    setVerdict(null);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/embeddings');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const view = await res.json();
        if (!cancelled) adopt(view);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  const running = ACTIVE.has(data?.migration?.state);
  useEffect(() => {
    if (!running) return undefined;
    const handle = setInterval(load, POLL_MS);
    return () => clearInterval(handle);
  }, [running, load]);

  useEffect(() => {
    if (!provider) return undefined;
    let cancelled = false;
    (async () => {
      let names = [];
      try {
        const res = await apiFetch(apiHost, apiPort, `/v1/admin/embeddings/models?provider=${encodeURIComponent(provider)}`);
        if (res.ok) names = (await res.json()).models ?? [];
      } catch { /* none listed: a typed name */ }
      if (!cancelled) setListing({ provider, names });
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort, provider]);
  const models = listing.provider === provider ? listing.names : null;   // null while this provider's list loads

  const picked = `${provider}|${model.trim()}`;
  const checked = verdict && verdict.for === picked ? verdict : null;

  const choose = (nextProvider, nextModel) => {
    setProvider(nextProvider);
    setModel(nextModel);
    setVerdict(null);
    setError('');
  };

  const check = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/embeddings/probe',
        sendJson('POST', { provider, model: model.trim() }));
      if (!res.ok) { setError(await reasonOf(res)); return; }
      setVerdict({ ...(await res.json()), for: picked });
    } catch {
      setError('Couldn’t reach the server.');
    } finally {
      setBusy(false);
    }
  };

  const put = async (body, done) => {
    setBusy(true);
    setError('');
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/embeddings', sendJson('PUT', body));
      if (!res.ok) { setError(await reasonOf(res)); return; }
      const view = await res.json();
      adopt(view);
      showToast?.(done(view), 5000);
    } catch {
      setError('Couldn’t reach the server.');
    } finally {
      setBusy(false);
    }
  };

  const use = () => put({ provider, model: model.trim() }, (view) => (ACTIVE.has(view.migration.state)
    ? `Re-embedding the library for ${view.migration.model}. Search keeps working meanwhile.`
    : `Embeddings now come from ${view.current.model} on ${view.current.provider}.`));
  const backToServer = () => put({ provider: null, model: null }, () => 'Embeddings follow the server’s setting again.');

  const cancel = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/embeddings/migration', sendJson('DELETE'));
      if (!res.ok) { setError(await reasonOf(res)); return; }
      adopt(await res.json());
      showToast?.('Re-embed cancelled: the library stays on its current model.', 4000);
    } catch {
      setError('Couldn’t reach the server.');
    } finally {
      setBusy(false);
    }
  };

  const box = `flex flex-col gap-3 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`;
  if (!data) {
    return (
      <div className={box} aria-label="Embeddings">
        <h3 className={sectionTitle}>Embeddings</h3>
        {failed
          ? <p className="text-xs text-red-500">Couldn&apos;t load the embedding settings.</p>
          : <p className={`text-xs ${theme.textMuted}`}>Loading…</p>}
      </div>
    );
  }

  const { current, source, env, migration, providers } = data;
  const control = `px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-h-11 md:min-h-0`;
  const button = 'min-h-11 md:min-h-0 px-3 py-1 rounded text-xs font-bold disabled:opacity-40';
  const listed = models ?? [];
  const modelOptions = [...new Set([...listed, ...(model && !listed.includes(model) ? [model] : [])])]
    .map((name) => ({ value: name, label: name }));
  const fromEnv = source.model === 'env' || source.model === 'default';

  return (
    <div className={box} aria-label="Embeddings">
      <h3 className={sectionTitle}>Embeddings</h3>
      <p className={`text-[10px] ${theme.textSecondary}`}>
        The model that turns passages and questions into vectors for search. Another model re-embeds the library in
        the background; search keeps working on the current one until it switches.
      </p>
      <p className={`text-xs ${theme.text}`}>
        In use: <strong>{current.model}</strong> on {current.provider}
        {current.dimensions ? `, shortened to ${current.dimensions}` : ''}.
      </p>

      {migration.state !== 'none' && (
        <div className="flex flex-wrap items-center gap-3">
          <p role="status" className={`text-xs ${migration.state === 'failed' ? 'text-amber-600' : theme.text}`}>
            {progressLine(migration)}
          </p>
          {migration.model && (
            <button type="button" onClick={cancel} disabled={busy}
              className={`${button} border ${theme.border} ${theme.text}`}>
              Cancel the re-embed
            </button>
          )}
        </div>
      )}

      <div className="flex flex-col gap-1">
        <label htmlFor="embeddings-provider" className={`text-xs font-bold ${theme.text}`}>Embedding provider</label>
        <SearchableSelect
          theme={theme} findLabel="Find a provider" id="embeddings-provider" value={provider} disabled={busy}
          onChange={(name) => choose(name, name === data.target.provider ? data.target.model : '')}
          className={control}
          options={providers.map((p) => ({ value: p.name, label: `${p.name} (${p.kind})` }))}
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="embeddings-model" className={`text-xs font-bold ${theme.text}`}>Embedding model</label>
        {models !== null && listed.length === 0 ? (
          <input id="embeddings-model" type="text" value={model} disabled={busy} spellCheck={false}
            placeholder="A model name, as the provider calls it"
            onChange={(e) => choose(provider, e.target.value)} className={`${control} w-64`} />
        ) : (
          <SearchableSelect
            theme={theme} findLabel="Find an embedding model" id="embeddings-model" value={model} disabled={busy}
            onChange={(name) => choose(provider, name)} className={control} options={modelOptions}
          />
        )}
        <p className={`text-[10px] ${theme.textSecondary}`}>
          {fromEnv ? <>From the server&apos;s <code>{env.model}</code>.</> : 'Chosen here.'}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={check} disabled={busy || !model.trim()}
          className={`${button} border ${theme.border} ${theme.text}`}>
          Check
        </button>
        <button type="button" onClick={use} disabled={busy || !checked?.ok}
          className={`${button} bg-blue-500 text-white`}>
          Use this model
        </button>
        {!fromEnv && (
          <button type="button" onClick={backToServer} disabled={busy}
            className={`min-h-11 md:min-h-0 text-xs underline ${theme.textSecondary} disabled:opacity-40`}>
            Back to the server’s setting
          </button>
        )}
      </div>
      {checked && (
        <p role="status" className={`text-xs ${checked.ok ? 'text-green-600' : 'text-red-500'}`}>{checked.reason}</p>
      )}
      {error && <p role="alert" className="text-xs text-red-500">{error}</p>}
    </div>
  );
}
