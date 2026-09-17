import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

export function AccountPanel({ theme, apiHost, apiPort, user, onLogout }) {
  const [tokens, setTokens] = useState([]);
  const [name, setName] = useState('');
  const [freshToken, setFreshToken] = useState(null); // shown once, on create
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/tokens');
    if (res.ok) setTokens(await res.json());
  }, [apiHost, apiPort]);

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

  const create = async () => {
    setError(null);
    const res = await apiFetch(apiHost, apiPort, '/v1/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (res.ok) {
      const body = await res.json();
      setFreshToken(body.token);
      setName('');
      load();
    } else {
      setError('Could not create token');
    }
  };

  const revoke = async (id) => {
    await apiFetch(apiHost, apiPort, `/v1/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' });
    load();
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

      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Token name"
          className={`flex-1 text-xs p-2 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text}`}
        />
        <button
          disabled={!name.trim()}
          onClick={create}
          className="px-3 py-2 rounded-lg border text-xs font-bold disabled:opacity-50"
        >
          Create
        </button>
      </div>
      {error && <p className="text-[10px] text-red-500">{error}</p>}

      <ul className="flex flex-col gap-1">
        {tokens.map((t) => (
          <li key={t.id} className="flex items-center justify-between text-xs">
            <span className={theme.text}>{t.name}</span>
            <button onClick={() => revoke(t.id)} className="text-[10px] text-red-500 underline">Revoke</button>
          </li>
        ))}
      </ul>

      <button onClick={onLogout} className={`text-[10px] ${theme.textMuted} underline self-start`}>Log out</button>
    </div>
  );
}
