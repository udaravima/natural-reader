import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Admin console: chat options (C2.1 Task 19) — how web search asks. The
 * server keeps one value for the whole deployment (services/chat_options.py):
 * the admin's choice here, else the CHAT_WEB_SEARCH environment variable,
 * else "ask". A change applies from each person's next message. "Use the
 * server setting" removes the admin's choice, so the deployer's value
 * applies again; without it there would be no way back from this screen.
 */

const MODES = [
  { id: 'ask', label: 'Ask first',
    hint: 'The assistant can offer a web search, and nothing leaves the server until the person says yes.' },
  { id: 'auto', label: 'Search without asking',
    hint: 'The assistant can search the web and read the pages it finds, whenever it decides to.' },
  { id: 'off', label: 'Off',
    hint: 'The assistant never searches the web.' },
];
const LABEL = Object.fromEntries(MODES.map((m) => [m.id, m.label]));
const SOURCE_ID = 'chat-web-search-source';
const UNAVAILABLE_ID = 'chat-web-search-unavailable';
const SOURCE_LINE = {
  admin: 'Set here.',
  env: 'From the server setting CHAT_WEB_SEARCH.',
  default: 'The default.',
};

export function ChatOptionsSection({ theme, apiHost, apiPort, showToast, onSaved }) {
  const [data, setData] = useState(null);   // { web_search, source, searxng }; null while loading
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const sectionRef = useRef(null);
  const refocus = useRef(false);   // a save began from a control in this section (a change or the reset)

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/chat-options');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  // The radios are disabled while a save is out, and a browser drops the focus from a control that gets
  // disabled: put it back on the chosen radio, so ArrowDown works twice in a row. The reset button is gone
  // once its save lands, which would leave the focus on the page; the radio takes it then too. Only if the
  // focus is still nowhere or in this section when the save ends: an admin who moved on to another field
  // keeps it there, or their next arrow key would change a deployment-wide setting.
  useEffect(() => {
    if (saving || !refocus.current) return;
    refocus.current = false;
    const section = sectionRef.current;
    const active = document.activeElement;
    if (active && active !== document.body && !section?.contains(active)) return;
    section?.querySelector('input[type="radio"]:checked')?.focus();
  }, [saving]);

  // One PUT for both a choice and the reset (mode null). `optimistic` shows a choice at once; a failure puts
  // back exactly what was there, mode and source both.
  const put = async (mode, optimistic) => {
    const before = data;
    // Only a control in this section calls this, so the admin's attention is here whether or not the browser
    // focused that control on the click (a script click, or Safari, which does not focus buttons on click).
    refocus.current = true;
    setSaving(true);
    if (optimistic) setData({ ...before, web_search: mode, source: 'admin' });
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/chat-options', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ web_search: mode }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (typeof body.detail === 'string' && body.detail) detail = body.detail;
        } catch { /* not JSON */ }
        throw new Error(detail);
      }
      const view = await res.json();
      setData(view);
      showToast?.(`Web search: ${LABEL[view.web_search] ?? view.web_search}. Applies from the next message.`, 4000);
      onSaved?.();
    } catch (e) {
      setData(before);
      showToast?.(`That didn't work: ${e.message}`, 5000);
    } finally {
      setSaving(false);
    }
  };

  const choose = (mode) => {
    if (saving || mode === data.web_search) return;
    put(mode, true);
  };

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';
  const locked = saving || !data?.searxng;

  return (
    <section ref={sectionRef} className="flex flex-col gap-3" aria-label="Chat options">
      <h2 className={sectionTitle}>Chat options</h2>
      {failed ? (
        <p className="text-xs text-red-500">Couldn&apos;t load the chat options.</p>
      ) : data === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : (
        <div className={`flex flex-col gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
          <fieldset
            aria-describedby={[!data.searxng && UNAVAILABLE_ID, SOURCE_ID].filter(Boolean).join(' ')}
            className="flex flex-col gap-2 min-w-0"
          >
            <legend className={`text-xs font-bold mb-2 ${theme.text}`}>Web search</legend>
            {MODES.map((m) => (
              <div key={m.id} className="flex flex-col gap-0.5">
                <label className={`min-h-11 md:min-h-0 flex items-center gap-2 text-xs ${theme.text} ${locked ? 'opacity-60' : 'cursor-pointer'}`}>
                  <input
                    type="radio" name="chat-web-search" value={m.id}
                    checked={data.web_search === m.id}
                    onChange={() => choose(m.id)}
                    disabled={locked}
                    aria-describedby={`chat-web-search-${m.id}-hint`}
                    className="h-3 w-3 accent-blue-500"
                  />
                  {m.label}
                </label>
                <span id={`chat-web-search-${m.id}-hint`} className={`pl-5 text-[10px] ${theme.textSecondary}`}>{m.hint}</span>
              </div>
            ))}
          </fieldset>
          {!data.searxng && (
            <p id={UNAVAILABLE_ID} className="text-xs text-amber-600">Web search isn&apos;t set up on this server (SEARXNG_URL).</p>
          )}
          <div className="flex flex-wrap items-center gap-3 text-[10px]">
            <span id={SOURCE_ID} className={theme.textSecondary}>{SOURCE_LINE[data.source] ?? data.source}</span>
            {data.source === 'admin' && (
              <button
                onClick={() => put(null, false)}
                disabled={saving}
                className="min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline"
              >
                Use the server setting
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
