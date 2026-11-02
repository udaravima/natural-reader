import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Admin console: Overview (spec §4.1). The server runs a handful of health
 * checks when this section opens (each with its own short timeout) and says,
 * one line per check, what is fine and what is not. A check that belongs to a
 * section has an Open link that goes there, so the way from "something is
 * wrong" to the control that fixes it is one click.
 *
 * The state is always a word, never colour alone: a colour-blind admin, or a
 * screen reader, gets the same answer.
 */

const STATE = {
  ok: { word: 'OK', cls: 'text-green-600' },
  warn: { word: 'Warning', cls: 'text-amber-600' },
  error: { word: 'Problem', cls: 'text-red-500' },
  off: { word: 'Off', cls: '' },   // muted: the theme's colour is applied at render
};

const fmtWindow = (n) => (n === null || n === undefined ? 'Unknown' : `${n.toLocaleString('en-US')} tokens`);

function ContextItems({ theme, items }) {
  return (
    <table className="text-[10px] w-full mt-2">
      <thead>
        <tr className={`${theme.textMuted} text-left`}>
          <th className="py-1 pr-2">Model</th>
          <th className="py-1 pr-2">Window</th>
          <th className="py-1 pr-2">Where it came from</th>
          <th className="py-1">Room</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => (
          <tr key={it.model} className={`border-t ${theme.border}`}>
            <td className="py-1 pr-2 break-all">{it.model}</td>
            <td className="py-1 pr-2 whitespace-nowrap">{fmtWindow(it.window)}</td>
            <td className="py-1 pr-2">{it.source}</td>
            <td className="py-1">{it.enough ? 'Yes' : 'No'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function OverviewSection({ theme, apiHost, apiPort, sectionLabel, onOpenSection }) {
  const [checks, setChecks] = useState(null);   // null until an answer arrives
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const latest = useRef(0);   // only the newest request may write; an older answer is stale

  const load = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/overview');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      if (mine !== latest.current) return;
      setChecks(body.checks);
      setFailed(false);
    } catch {
      if (mine !== latest.current) return;
      setFailed(true);
    }
    setLoading(false);
  }, [apiHost, apiPort]);

  useEffect(() => {
    load();
    return () => { latest.current += 1; };   // leaving the section drops an answer still on its way
  }, [load]);

  const checkAgain = () => { setLoading(true); load(); };

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

  return (
    <section className="flex flex-col gap-3" aria-label="Overview">
      <div className="flex items-center justify-between gap-3">
        <h2 className={sectionTitle}>Overview</h2>
        <button
          onClick={checkAgain}
          disabled={loading}
          className="text-xs underline text-blue-500 min-h-11 md:min-h-0 disabled:opacity-40 disabled:no-underline"
        >
          Check again
        </button>
      </div>
      {loading ? (
        <p className={`text-xs ${theme.textMuted}`}>Checking…</p>
      ) : failed ? (
        <p className="text-xs text-red-500">Couldn&apos;t run the checks.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {checks.map((c) => {
            const state = STATE[c.state] ?? { word: c.state, cls: '' };
            return (
              <li key={c.id} className={`flex flex-col p-3 rounded-lg border ${theme.border} ${theme.bgSecondary} text-xs`}>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className={`font-bold w-16 shrink-0 ${state.cls || theme.textMuted}`}>{state.word}</span>
                  <span className="font-bold">{c.title}</span>
                  <span className={`flex-1 min-w-0 ${theme.textSecondary}`}>{c.summary}</span>
                  {c.section && (
                    <button onClick={() => onOpenSection(c.section)} className="underline text-blue-500 min-h-11 md:min-h-0">
                      Open {sectionLabel(c.section)}
                    </button>
                  )}
                </div>
                {Array.isArray(c.items) && c.items.length > 0 && <ContextItems theme={theme} items={c.items} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
