import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

export function AdminPanel({ theme, apiHost, apiPort, currentUserId }) {
  const [users, setUsers] = useState([]);

  const load = useCallback(async () => {
    const res = await apiFetch(apiHost, apiPort, '/v1/admin/users');
    if (res.ok) setUsers(await res.json());
  }, [apiHost, apiPort]);

  // Load on mount via an async IIFE so setUsers runs after the await, not
  // synchronously in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/users');
      if (!cancelled && res.ok) setUsers(await res.json());
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  const patch = async (id, body) => {
    await apiFetch(apiHost, apiPort, `/v1/admin/users/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    load();
  };

  return (
    <div className="px-4 pb-4 flex flex-col gap-2">
      {users.map((u) => {
        const isSelf = u.id === currentUserId;
        return (
          <div key={u.id} className="flex items-center justify-between text-xs gap-2">
            <span className={`${theme.text} truncate`}>{u.email}</span>
            <span className={`text-[10px] ${theme.textMuted}`}>{u.role}/{u.status}</span>
            <span className="flex gap-1 shrink-0">
              {u.status !== 'active' && (
                <button onClick={() => patch(u.id, { status: 'active' })} className="text-[10px] text-green-600 underline">Activate</button>
              )}
              {u.status === 'active' && !isSelf && (
                <button onClick={() => patch(u.id, { status: 'disabled' })} className="text-[10px] text-red-500 underline">Disable</button>
              )}
              {!isSelf && (
                <button
                  onClick={() => patch(u.id, { role: u.role === 'admin' ? 'member' : 'admin' })}
                  className="text-[10px] underline"
                >
                  {u.role === 'admin' ? 'Make member' : 'Make admin'}
                </button>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
