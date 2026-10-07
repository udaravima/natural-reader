import { useCallback, useEffect, useState } from 'react';
import { Loader2, UserPlus, X } from 'lucide-react';
import PeoplePicker from '../people/PeoplePicker';
import { ROLES, roleLabel } from '../../lib/projectRoles';

/**
 * A project's members (A0 §10). Controls follow `project.can`: Maintainers
 * manage Readers, Contributors and Maintainers; Owner rows and the Owner
 * role need `can.manage_owners`. A disabled member's role can't be changed
 * (the server refuses), so it shows as text; removal stays. My own row has no controls (Leave is in
 * the header). Every control is disabled while its request is in flight,
 * and a refusal shows the server's notice and reloads the project.
 */
export default function MembersTab({ theme, api, project, currentUserId, showToast, onRefused, onChanged }) {
  const [members, setMembers] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState(null);
  const [newRole, setNewRole] = useState('contributor');
  const { can } = project;
  const roleOptions = can.manage_owners ? ROLES : ROLES.filter((r) => r !== 'owner');

  const load = useCallback(async () => {
    try {
      const list = await api.members(project.id);
      setMembers(list);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [api, project.id]);

  useEffect(() => { load(); }, [load]);

  const run = async (key, fn, ok) => {
    setBusyId(key);
    try {
      await fn();
      showToast(ok, 3000);
      onChanged?.();
      return true;
    } catch (e) {
      showToast(e.message, 5000);
      onRefused?.();
      return false;
    } finally {
      setBusyId(null);
      await load();
    }
  };

  const editable = (m) => m.user_id !== currentUserId && (m.role === 'owner' ? can.manage_owners : can.manage_members);
  const changeRole = (m, role) => run(m.user_id, () => api.putMember(project.id, m.user_id, role), `${m.name} is now ${roleLabel(role)}.`);
  const remove = (m) => run(m.user_id, () => api.removeMember(project.id, m.user_id), `Removed ${m.name} from ${project.name}.`);
  const add = async () => {
    const ok = await run('add', () => api.putMember(project.id, picked.id, newRole), `Added ${picked.name} as ${roleLabel(newRole)}.`);
    if (ok) { setPicked(null); setAdding(false); setNewRole('contributor'); }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className={`text-xs ${theme.textSecondary}`}>Members ({members ? members.length : '…'})</span>
        {can.manage_members && !adding && (
          <button onClick={() => setAdding(true)} className="flex items-center gap-1 text-xs underline text-blue-500">
            <UserPlus size={12} /> + Add people
          </button>
        )}
      </div>

      {adding && (
        <div className={`flex flex-col gap-2 p-2 rounded border ${theme.border}`}>
          {picked ? (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-bold">{picked.name}</span>
              <select value={newRole} onChange={(e) => setNewRole(e.target.value)} aria-label="Role for the new member"
                className={`px-1.5 py-0.5 text-xs rounded border ${theme.border} ${theme.bg}`}>
                {roleOptions.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
              </select>
              <button onClick={add} disabled={busyId === 'add'} className="px-2 py-0.5 rounded bg-blue-500 text-white disabled:opacity-50">
                {busyId === 'add' ? <Loader2 size={12} className="animate-spin" /> : 'Add'}
              </button>
              <button onClick={() => setPicked(null)} className="underline">Choose someone else</button>
            </div>
          ) : (
            <PeoplePicker theme={theme} lookup={api.lookup} onPick={setPicked} label="Find a person to add"
              excludeIds={(members || []).map((m) => m.user_id)} excludedLabel="Already a member of this project." />
          )}
          <button onClick={() => { setAdding(false); setPicked(null); }} className="self-start text-[10px] underline">Close</button>
        </div>
      )}

      {error && (
        <div role="alert" className="flex items-center gap-2 text-xs text-red-500">
          <span>{error}</span>
          <button onClick={load} className="underline">Retry</button>
        </div>
      )}
      {members === null ? (
        error ? null : <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {members.map((m) => (
            <li key={m.user_id} className={`flex items-center justify-between gap-2 p-2 rounded border ${theme.border} ${theme.bgSecondary}`}>
              <span className="flex flex-wrap items-center gap-2 min-w-0 text-xs">
                <span className="font-bold truncate">{m.name}</span>
                {m.user_id === currentUserId && <span className={theme.textMuted}>(you)</span>}
                {m.username && <span className={`text-[10px] ${theme.textMuted}`}>@{m.username}</span>}
                {m.status === 'pending' && <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">awaiting approval</span>}
                {m.status === 'disabled' && <span className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-500">disabled</span>}
                {m.added_via === 'admin' && <span className={`text-[9px] ${theme.textMuted}`}>added by admin</span>}
              </span>
              <span className="flex items-center gap-2 shrink-0">
                {editable(m) && m.status !== 'disabled' ? (
                  <select value={m.role} onChange={(e) => changeRole(m, e.target.value)} disabled={busyId !== null}
                    aria-label={`Role for ${m.name}`}
                    className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} disabled:opacity-50`}>
                    {roleOptions.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                  </select>
                ) : (
                  <span className={`text-[10px] ${theme.textSecondary}`}>{roleLabel(m.role)}</span>
                )}
                {editable(m) && (
                  <button onClick={() => remove(m)} disabled={busyId !== null} aria-label={`Remove ${m.name} from ${project.name}`}
                    className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
                    {busyId === m.user_id ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
