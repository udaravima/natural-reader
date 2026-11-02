import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Inference usage: tokens by model, by person or by day. The server counts a day in the
 * deployment's timezone. By model is the default; By person and By day can be narrowed to one
 * model, and a person's row opens into their own per-model rows.
 */

const DAY_OPTIONS = [1, 7, 14, 30, 90];
const VIEWS = [['model', 'By model'], ['user', 'By person'], ['day', 'By day']];
const ALL = 'all';                       // Model filter: no `model` param at all
const pick = (model) => `m:${model}`;    // Model filter: a model; '' is the Unknown rows
const TAGS = { local: 'Local', cloud: 'Cloud', unknown: 'Unknown' };

const num = (n) => Number(n ?? 0).toLocaleString();

// Estimated share (U4): a whole percent of the total; a dash when none is estimated;
// "<1%" when something is but it rounds to nothing.
function estimatedShare(r) {
  const est = Number(r.estimated_tokens ?? 0);
  const total = Number(r.tokens ?? 0);
  if (!est || !total) return '—';
  const pct = Math.round((est / total) * 100);
  return pct === 0 ? '<1%' : `${pct}%`;
}

const url = (days, group, { userId, model } = {}) => {
  let path = `/v1/admin/inference/usage?days=${days}&group=${group}`;
  if (userId !== undefined) path += `&user_id=${encodeURIComponent(userId)}`;
  if (model !== undefined) path += `&model=${encodeURIComponent(model)}`;
  return path;
};

function ModelTable({ rows, theme }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-[10px] w-full">
        <thead>
          <tr className={`${theme.textMuted} text-left`}>
            <th className="py-1 pr-2">Model</th>
            <th className="py-1 pr-2">Where</th>
            <th className="py-1 pr-2 text-right">Chat</th>
            <th className="py-1 pr-2 text-right">Reader</th>
            <th className="py-1 pr-2 text-right">Requests</th>
            <th className="py-1 pr-2 text-right">Total</th>
            <th className="py-1 text-right">Estimated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.model} className={`border-t ${theme.border}`}>
              <td className="py-1 pr-2 break-all">{r.model || 'Unknown'}</td>
              <td className="py-1 pr-2">{TAGS[r.tag] ?? 'Unknown'}</td>
              <td className="py-1 pr-2 text-right">{num(r.chat_tokens)}</td>
              <td className="py-1 pr-2 text-right">{num(r.reader_tokens)}</td>
              <td className="py-1 pr-2 text-right">{num(r.requests)}</td>
              <td className="py-1 pr-2 text-right">{num(r.tokens)}</td>
              <td className="py-1 text-right">{estimatedShare(r)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function UsageSection({ theme, apiHost, apiPort, showToast }) {
  const [days, setDays] = useState(7);
  const [view, setView] = useState('model');
  const [filter, setFilter] = useState(ALL);

  // Each answer carries the request it answers, so a table is only ever drawn from the
  // answer to the request on screen; the sequence numbers below drop late arrivals as well.
  const [modelData, setModelData] = useState(null);   // { days, rows } — By model, current range
  const [viewData, setViewData] = useState(null);     // { key, rows } — By person / By day
  const [people, setPeople] = useState({});           // user id -> { rows } | { failed } | { loading }
  const [open, setOpen] = useState({});               // user id -> true while expanded

  const modelSeq = useRef(0);
  const viewSeq = useRef(0);
  const epoch = useRef(0);   // bumps with the range, so an expansion answered late is dropped

  const fetchRows = useCallback(async (path) => {
    const res = await apiFetch(apiHost, apiPort, path);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }, [apiHost, apiPort]);

  // By model for the range: the By model table, the Model filter's options, and the Unknown note.
  useEffect(() => {
    const mine = ++modelSeq.current;
    (async () => {
      try {
        const rows = await fetchRows(url(days, 'model'));
        if (mine === modelSeq.current) setModelData({ days, rows });
      } catch (e) {
        if (mine !== modelSeq.current) return;
        setModelData({ days, rows: [] });
        showToast(`Could not load usage: ${e.message}`, 5000);
      }
    })();
  }, [fetchRows, days, showToast]);

  const model = filter === ALL ? undefined : filter.slice(2);
  const viewKey = view === 'model' ? null : url(days, view, { model });

  useEffect(() => {
    if (viewKey === null) return;
    const mine = ++viewSeq.current;
    (async () => {
      try {
        const rows = await fetchRows(viewKey);
        if (mine === viewSeq.current) setViewData({ key: viewKey, rows });
      } catch (e) {
        if (mine !== viewSeq.current) return;
        setViewData({ key: viewKey, rows: [] });
        showToast(`Could not load usage: ${e.message}`, 5000);
      }
    })();
  }, [fetchRows, viewKey, showToast]);

  const changeDays = (d) => {
    if (d === days) return;
    epoch.current += 1;
    setPeople({});
    setOpen({});
    setFilter(ALL);   // the new range may not have the chosen model at all
    setDays(d);
  };

  const togglePerson = async (id) => {
    const nowOpen = !open[id];
    setOpen((o) => ({ ...o, [id]: nowOpen }));
    if (!nowOpen || (people[id] && !people[id].failed)) return;
    const mine = epoch.current;
    setPeople((p) => ({ ...p, [id]: { loading: true } }));
    try {
      const rows = await fetchRows(url(days, 'model', { userId: id }));
      if (mine === epoch.current) setPeople((p) => ({ ...p, [id]: { rows } }));
    } catch (e) {
      if (mine !== epoch.current) return;
      setPeople((p) => ({ ...p, [id]: { failed: true } }));
      showToast(`Could not load usage: ${e.message}`, 5000);
    }
  };

  const modelRows = modelData?.days === days ? modelData.rows : null;
  const viewRows = view === 'model' ? modelRows : (viewData?.key === viewKey ? viewData.rows : null);
  const hasUnknown = modelRows?.some((r) => r.model === '') ?? false;

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';
  const control = `min-h-11 md:min-h-0 px-2 py-1 text-[10px] rounded border ${theme.border} ${theme.bgSecondary}`;

  return (
      <section className="flex flex-col gap-3" aria-label="Inference usage">
        <div className="flex items-center justify-between">
          <h2 className={sectionTitle}>Inference usage</h2>
          <select
            value={days} onChange={(e) => changeDays(Number(e.target.value))}
            aria-label="Usage days"
            className={control}
          >
            {DAY_OPTIONS.map((d) => <option key={d} value={d}>last {d} day{d > 1 ? 's' : ''}</option>)}
          </select>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {VIEWS.map(([id, label]) => (
            <button
              key={id} type="button" aria-pressed={view === id}
              onClick={() => setView(id)}
              className={`${control} ${view === id ? 'font-bold border-blue-500 text-blue-500' : ''}`}
            >
              {label}
            </button>
          ))}
          {view !== 'model' && (
            <select
              value={filter} onChange={(e) => setFilter(e.target.value)}
              aria-label="Model"
              className={control}
            >
              <option value={ALL}>All models</option>
              {(modelRows ?? []).map((r) => (
                <option key={r.model} value={pick(r.model)}>{r.model || 'Unknown'}</option>
              ))}
            </select>
          )}
        </div>

        {viewRows === null ? (
          <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
        ) : viewRows.length === 0 ? (
          <p className={`text-xs ${theme.textMuted}`}>No usage recorded yet.</p>
        ) : view === 'model' ? (
          <ModelTable rows={viewRows} theme={theme} />
        ) : (
          <div className="overflow-x-auto">
            <table className="text-[10px] w-full">
              <thead>
                <tr className={`${theme.textMuted} text-left`}>
                  <th className="py-1 pr-2">{view === 'user' ? 'Person' : 'Day'}</th>
                  <th className="py-1 pr-2 text-right">Chat</th>
                  <th className="py-1 pr-2 text-right">Reader</th>
                  <th className="py-1 pr-2 text-right">Requests</th>
                  <th className="py-1 pr-2 text-right">Total</th>
                  <th className="py-1 pr-2 text-right">Estimated</th>
                  {view === 'user' && <th className="py-1"><span className="sr-only">Models</span></th>}
                </tr>
              </thead>
              <tbody>
                {viewRows.map((r) => {
                  const id = view === 'user' ? r.user_id : null;
                  const detail = id !== null && open[id] ? people[id] : null;
                  // The cached answer holds all of the person's models; show the ones the
                  // Model filter lets through, so the parts sum to the filtered row above.
                  const shown = detail?.rows && (model === undefined ? detail.rows : detail.rows.filter((m) => m.model === model));
                  return [
                    <tr key={view === 'user' ? id : r.day} className={`border-t ${theme.border}`}>
                      {/* day is already a YYYY-MM-DD in the deployment's zone: render as-is,
                          never new Date(day) (local-midnight assumptions). */}
                      <td className="py-1 pr-2 break-all">{view === 'user' ? r.email : String(r.day)}</td>
                      <td className="py-1 pr-2 text-right">{num(r.chat_tokens)}</td>
                      <td className="py-1 pr-2 text-right">{num(r.reader_tokens)}</td>
                      <td className="py-1 pr-2 text-right">{num(r.requests)}</td>
                      <td className="py-1 pr-2 text-right">{num(r.tokens)}</td>
                      <td className="py-1 pr-2 text-right">{estimatedShare(r)}</td>
                      {view === 'user' && (
                        <td className="py-1 text-right">
                          <button
                            type="button" aria-expanded={!!open[id]}
                            aria-label={`Models for ${r.email}`}
                            onClick={() => togglePerson(id)}
                            className="min-h-11 min-w-11 md:min-h-0 md:min-w-0 inline-flex items-center justify-center"
                          >
                            {open[id] ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                          </button>
                        </td>
                      )}
                    </tr>,
                    detail && (
                      <tr key={`${id}-models`}>
                        <td colSpan={7} className="pl-4 pb-2">
                          {shown ? (
                            shown.length === 0
                              ? <p className={`text-[10px] ${theme.textMuted}`}>No usage recorded yet.</p>
                              : <ModelTable rows={shown} theme={theme} />
                          ) : (
                            <p className={`text-[10px] ${theme.textMuted}`}>{detail.failed ? 'Could not load this person’s models.' : 'Loading…'}</p>
                          )}
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}

        {viewRows !== null && viewRows.length > 0 && (
          <div className={`flex flex-col gap-1 text-[10px] ${theme.textSecondary}`}>
            <p>Each day is counted in the deployment&apos;s timezone. Changing it doesn&apos;t move days already counted.</p>
            {hasUnknown && <p>Usage recorded before this version has no model and shows as Unknown.</p>}
            <p>Estimated: counted from text length because the provider sent no token counts.</p>
          </div>
        )}
      </section>
  );
}
