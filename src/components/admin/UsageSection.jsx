import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/** Inference usage: tokens per user per day. The server counts a day in the deployment's timezone. */

const DAY_OPTIONS = [1, 7, 14, 30, 90];

export function UsageSection({ theme, apiHost, apiPort, showToast }) {
  const [usage, setUsage] = useState(null);
  const [days, setDays] = useState(7);

  const loadUsage = useCallback(async (d) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/admin/inference/usage?days=${d}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setUsage(await res.json());
    } catch (e) {
      setUsage([]);
      showToast(`Could not load usage: ${e.message}`, 5000);
    }
  }, [apiHost, apiPort, showToast]);

  useEffect(() => { loadUsage(days); }, [loadUsage, days]);

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

  return (
      <section className="flex flex-col gap-3" aria-label="Inference usage">
        <div className="flex items-center justify-between">
          <h2 className={sectionTitle}>Inference usage</h2>
          <select
            value={days} onChange={(e) => setDays(Number(e.target.value))}
            aria-label="Usage days"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-[10px] rounded border ${theme.border} ${theme.bgSecondary}`}
          >
            {DAY_OPTIONS.map((d) => <option key={d} value={d}>last {d} day{d > 1 ? 's' : ''}</option>)}
          </select>
        </div>
        {usage === null ? (
          <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
        ) : usage.length === 0 ? (
          <p className={`text-xs ${theme.textMuted}`}>No usage recorded yet.</p>
        ) : (
          <table className="text-[10px] w-full">
            <thead>
              <tr className={`${theme.textMuted} text-left`}>
                <th className="py-1 pr-2">Email</th>
                <th className="py-1 pr-2">Day</th>
                <th className="py-1 pr-2 text-right">Prompt</th>
                <th className="py-1 pr-2 text-right">Eval</th>
                <th className="py-1 pr-2 text-right">Requests</th>
                <th className="py-1 text-right">Total tokens</th>
              </tr>
            </thead>
            <tbody>
              {usage.map((r, i) => (
                // day is already a YYYY-MM-DD in the deployment's zone: render as-is, never
                // new Date(day) (local-midnight assumptions).
                <tr key={`${r.user_id}-${r.day}-${i}`} className={`border-t ${theme.border}`}>
                  <td className="py-1 pr-2 truncate">{r.email}</td>
                  <td className="py-1 pr-2">{String(r.day)}</td>
                  <td className="py-1 pr-2 text-right">{r.prompt_tokens}</td>
                  <td className="py-1 pr-2 text-right">{r.eval_tokens}</td>
                  <td className="py-1 pr-2 text-right">{r.requests}</td>
                  <td className="py-1 text-right">{r.tokens}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {usage !== null && usage.length > 0 && (
          <p className={`text-[10px] ${theme.textSecondary}`}>
            Each day is counted in the deployment&apos;s timezone. Changing it doesn&apos;t move days already counted.
          </p>
        )}
      </section>
  );
}
