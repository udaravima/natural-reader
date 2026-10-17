import { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

// What a token may do (server/auth/scopes.py). A scope narrows the account's
// own permissions, so only the ones the account holds are offered.
const SCOPES = [
  { id: 'read_aloud', label: 'Read aloud', hint: 'the browser extension', needs: 'reader' },
  { id: 'documents', label: 'Documents & projects', hint: 'library, projects, people search; includes read aloud', needs: 'reader' },
  { id: 'chat', label: 'Chat', hint: 'chats and models', needs: 'chat' },
  { id: 'admin', label: 'Admin', hint: 'the admin console API', needs: 'admin' },
];
const SCOPE_LABEL = Object.fromEntries(SCOPES.map((s) => [s.id, s.label]));

const EXPIRY = [
  { id: '30', label: '30 days', days: 30 },
  { id: '90', label: '90 days', days: 90 },
  { id: '365', label: '1 year', days: 365 },
  { id: 'never', label: 'Never', days: null },
];

function accessLabel(scopes) {
  if (scopes == null) return 'Full access (old)';
  return scopes.map((s) => SCOPE_LABEL[s] || s).join(' · ');
}

function dateLabel(iso) {
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function detailMessage(body) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (d && d.message) return d.message;
  if (Array.isArray(d) && d[0] && d[0].msg) return d[0].msg;
  return null;
}

export function AccountPanel({ theme, apiHost, apiPort, user }) {
  const caps = user?.capabilities || (user?.role === 'admin' ? ['admin', 'chat', 'reader'] : ['chat', 'reader']);
  const offered = SCOPES.filter((s) => caps.includes(s.needs));
  const [tokens, setTokens] = useState([]);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState(['read_aloud']);
  const [expiry, setExpiry] = useState('365');
  const [freshToken, setFreshToken] = useState(null); // shown once, on create
  const [error, setError] = useState(null);

  // Load on mount via an async IIFE so setTokens runs after the await, not
  // synchronously in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await apiFetch(apiHost, apiPort, '/v1/auth/tokens');
      if (!cancelled && res.ok) setTokens(await res.json());
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  const toggleScope = (id) => setScopes((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const create = async () => {
    setError(null);
    const { days } = EXPIRY.find((e) => e.id === expiry);
    const sent = {
      name,
      // Kept in the order the boxes are listed, whatever order they were ticked.
      scopes: SCOPES.map((s) => s.id).filter((id) => scopes.includes(id)),
      expires_at: days == null ? null : new Date(Date.now() + days * 86400000).toISOString(),
    };
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sent),
    });
    if (res.ok) {
      const body = await res.json();
      setFreshToken(body.token);
      setName('');
      // Added from the answer, not re-read: the server answers before its
      // write commits, so a list read straight after can miss the new token.
      setTokens((cur) => [...cur.filter((t) => t.id !== body.id),
        { id: body.id, name: sent.name, scopes: sent.scopes, expires_at: sent.expires_at, last_used_at: null }]);
    } else {
      let message = null;
      try { message = detailMessage(await res.json()); } catch { /* not JSON */ }
      setError(message || 'Could not create token');
    }
  };

  const revoke = async (id) => {
    const res = await apiFetch(apiHost, apiPort, `/v1/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' });
    // 404: already gone. Either way it leaves the list (see create on re-reading).
    if (res.ok || res.status === 404) setTokens((cur) => cur.filter((t) => t.id !== id));
    else setError('Could not revoke token');
  };

  return (
    <div className="px-4 pb-4 flex flex-col gap-3">
      <div className={`text-[10px] ${theme.textMuted}`}>{user?.email} · {user?.role}</div>

      {freshToken && (
        <div className={`p-2 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
          <p className={`text-[10px] ${theme.textSecondary}`}>Copy this token now — you won't see it again.</p>
          <code className={`text-xs break-all ${theme.text}`}>{freshToken}</code>
          <div className="mt-1 flex gap-3">
            <button className="text-[10px] underline" onClick={() => navigator.clipboard?.writeText(freshToken)}>Copy</button>
            <button className="text-[10px] underline" onClick={() => setFreshToken(null)}>Done</button>
          </div>
        </div>
      )}

      <div className={`text-xs font-semibold ${theme.text}`}>Access tokens</div>
      <p className={`text-[10px] ${theme.textSecondary}`}>
        For the read-aloud extension and scripts. Give each one only the access it needs.
      </p>
      <fieldset className="flex flex-col gap-1">
        <legend className={`text-[10px] ${theme.textMuted}`}>Access</legend>
        {offered.map((s) => (
          <label key={s.id} className={`flex items-start gap-2 text-xs ${theme.text}`}>
            <input type="checkbox" checked={scopes.includes(s.id)} onChange={() => toggleScope(s.id)} className="mt-0.5" />
            <span>{s.label} <span className={`text-[10px] ${theme.textMuted}`}>({s.hint})</span></span>
          </label>
        ))}
      </fieldset>
      <label className={`flex items-center gap-2 text-xs ${theme.text}`}>
        Expires
        <select
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
          className={`text-xs p-1 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text}`}
        >
          {EXPIRY.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
        </select>
      </label>
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Token name"
          className={`flex-1 text-xs p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text}`}
        />
        <button
          disabled={!name.trim() || !scopes.some((id) => offered.some((s) => s.id === id))}
          onClick={create}
          className="px-3 py-2 rounded-lg border text-xs font-bold disabled:opacity-50"
        >
          Create
        </button>
      </div>
      {error && <p className="text-[10px] text-red-500">{error}</p>}

      <ul className="flex flex-col gap-1">
        {tokens.map((t) => (
          <li key={t.id} className="flex items-center justify-between gap-2 text-xs">
            <span className="flex flex-col">
              <span className={theme.text}>{t.name}</span>
              <span className={`text-[10px] ${theme.textMuted}`}>
                <span data-scope-label>{accessLabel(t.scopes)}</span>
                {' · '}{t.last_used_at ? `used ${dateLabel(t.last_used_at)}` : 'never used'}
                {t.expires_at && (new Date(t.expires_at) <= new Date()
                  ? ' · expired'
                  : ` · expires ${dateLabel(t.expires_at)}`)}
              </span>
            </span>
            <button onClick={() => revoke(t.id)} className="text-[10px] text-red-500 underline">Revoke</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
