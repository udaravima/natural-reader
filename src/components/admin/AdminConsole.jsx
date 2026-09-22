import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Shield, Loader2 } from 'lucide-react';
import { apiFetch } from '../../utils/apiFetch';

/**
 * Full-height admin console (spec §3): Users (list + enroll + delete),
 * Inference usage, Deployment config — one scroll, no tabs. Visual language
 * mirrors AdminPanel/AccountPanel (tiny text, list rows, underline buttons).
 *
 * All rails are server-side; this UI only hides/disables affordances
 * cosmetically (self-row hides Disable / Delete and disables the admin
 * capability checkbox, so an admin can't lock themselves out).
 */

const DAY_OPTIONS = [1, 7, 14, 30, 90];
const CAPABILITIES = ['reader', 'chat', 'admin'];

function fmtBudget(budget) {
  if (budget === null || budget === undefined) return 'unlimited (unset)';
  if (budget === 0) return 'unlimited';
  return `${budget} tokens/day`;
}

// Per-user budget editor. PATCH exclude_unset semantics: the field is only
// sent when the admin presses Set — empty = explicit clear (null →
// deployment default), 0 = unlimited, number = tokens/day. The parent keys
// this component on the server-side budget value, so a refresh after a
// successful PATCH remounts it and the input re-syncs without an effect.
function BudgetField({ u, theme, onSet }) {
  const [val, setVal] = useState(u.inference_daily_token_budget === null ? '' : String(u.inference_daily_token_budget));
  return (
    <span className="flex items-center gap-1 shrink-0">
      <input
        type="number" min="0" value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder="default"
        aria-label={`Budget for ${u.email}`}
        title="empty = default, 0 = unlimited"
        className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-20`}
      />
      <button onClick={() => onSet(u, val)} className="text-[10px] underline">Set</button>
    </span>
  );
}

export function AdminConsole({ theme, apiHost, apiPort, currentUserId, onBack, showToast }) {
  const [users, setUsers] = useState(null);
  const [usage, setUsage] = useState(null);
  const [config, setConfig] = useState(null);
  const [days, setDays] = useState(7);

  // Enroll form
  const [enrollEmail, setEnrollEmail] = useState('');
  const [enrollName, setEnrollName] = useState('');
  const [enrollStatus, setEnrollStatus] = useState('pending');
  const [enrollBudget, setEnrollBudget] = useState('');
  const [enrollCaps, setEnrollCaps] = useState({ reader: false, chat: false, admin: false });
  const [enrollNote, setEnrollNote] = useState(null); // { kind: 'ok'|'err', text }
  const [enrolling, setEnrolling] = useState(false); // POST in flight → busy button

  // Delete flow
  const [deleteTarget, setDeleteTarget] = useState(null); // user id
  const [deleteTyped, setDeleteTyped] = useState('');

  const loadUsers = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/users');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setUsers(await res.json());
    } catch (e) {
      setUsers([]);
      showToast(`Could not load users: ${e.message}`, 5000);
    }
  }, [apiHost, apiPort, showToast]);

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

  // Load on mount via async IIFE so setState runs after the await, not
  // synchronously in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/admin/inference/config');
        if (!cancelled && res.ok) setConfig(await res.json());
      } catch { /* config is read-only garnish; failures are non-fatal */ }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort]);

  useEffect(() => { loadUsers(); }, [loadUsers]);
  useEffect(() => { loadUsage(days); }, [loadUsage, days]);

  const patchUser = async (id, body) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/admin/users/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadUsers();
    } catch (e) {
      showToast(`Update failed: ${e.message}`, 5000);
    }
  };

  // locked=true (self-lockout guard on the admin's own admin capability) is a
  // belt-and-suspenders no-op: the checkbox that calls this is also disabled,
  // so onChange never fires, but this keeps the guard co-located with the
  // mutation rather than relying solely on the disabled prop.
  const toggleCapability = (u, cap, locked) => {
    if (locked) return;
    const current = new Set(u.capabilities ?? []);
    if (current.has(cap)) current.delete(cap); else current.add(cap);
    patchUser(u.id, { capabilities: [...current].sort() });
  };

  const toggleEnrollCap = (cap) =>
    setEnrollCaps((prev) => ({ ...prev, [cap]: !prev[cap] }));

  const submitEnroll = async (e) => {
    e.preventDefault();
    if (!enrollEmail.trim()) return;
    const body = { email: enrollEmail.trim() };
    if (enrollName.trim()) body.display_name = enrollName.trim();
    if (enrollStatus !== 'pending') body.status = enrollStatus;
    const budget = enrollBudget.trim();
    if (budget !== '') body.inference_daily_token_budget = Number(budget);
    const caps = CAPABILITIES.filter((cap) => enrollCaps[cap]);
    if (caps.length) body.capabilities = caps;
    setEnrolling(true);
    try {
      const res = await apiFetch(apiHost, apiPort, '/v1/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.status === 409) {
        setEnrollNote({ kind: 'err', text: 'That email is already taken.' });
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // { user, onboarding, temp_password? } (current contract). A response
      // without an onboarding field (e.g. an older/simpler mock) falls back
      // to the original claim-by-email copy below.
      const data = await res.json().catch(() => null);
      const onboarding = data?.onboarding;
      let text;
      if (onboarding === 'temp_password' && data?.temp_password) {
        text = `Enrolled ${enrollEmail.trim()} — temp password: ${data.temp_password} (share this once).`;
      } else if (onboarding === 'email') {
        text = `Enrolled ${enrollEmail.trim()} — invite emailed.`;
      } else if (onboarding === 'manual') {
        text = `Enrolled ${enrollEmail.trim()} — create this user in Keycloak — links on first verified-email login.`;
      } else {
        text = `Enrolled ${enrollEmail.trim()} — have the user log in with this exact verified email to claim the account.`;
      }
      setEnrollNote({ kind: 'ok', text });
      setEnrollEmail(''); setEnrollName(''); setEnrollBudget('');
      setEnrollStatus('pending');
      setEnrollCaps({ reader: false, chat: false, admin: false });
      await loadUsers();
    } catch (e) {
      setEnrollNote({ kind: 'err', text: `Enroll failed: ${e.message}` });
    } finally {
      setEnrolling(false);
    }
  };

  const setBudget = async (u, raw) => {
    const trimmed = raw.trim();
    // Empty = deployment default (explicit null clear); 0 = unlimited.
    const value = trimmed === '' ? null : Number(trimmed);
    if (trimmed !== '' && (!Number.isInteger(value) || value < 0)) {
      showToast('Budget must be a whole number ≥ 0 (empty = default, 0 = unlimited).', 4000);
      return;
    }
    await patchUser(u.id, { inference_daily_token_budget: value });
  };

  const confirmDelete = async (u) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/admin/users/${encodeURIComponent(u.id)}`, {
        method: 'DELETE',
      });
      if (res.status === 409) {
        const body = await res.json().catch(() => ({}));
        const reason = body?.detail?.reason;
        const msg = reason === 'self' ? 'You cannot delete your own account.'
          : reason === 'seed_admin' ? 'The seed admin row cannot be deleted.'
          : reason === 'last_active_admin' ? 'Cannot delete the last active admin.'
          : 'Delete refused by the server.';
        showToast(msg, 5000);
        setDeleteTarget(null); setDeleteTyped('');
        return;
      }
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      setDeleteTarget(null); setDeleteTyped('');
      showToast(`Deleted ${u.email} — their documents and chat history were removed.`, 6000);
      await loadUsers();
    } catch (e) {
      showToast(`Delete failed: ${e.message}`, 5000);
    }
  };

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

  return (
    <div className={`h-full w-full overflow-y-auto ${theme.bg} ${theme.text}`}>
      <div className="max-w-4xl mx-auto px-4 md:px-8 py-6 flex flex-col gap-10">
        {/* Header row */}
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold flex items-center gap-2">
            <Shield size={18} className="text-blue-500" />
            Admin console
          </h1>
          <button onClick={onBack} className="text-xs underline text-blue-500 flex items-center gap-1">
            <ArrowLeft size={12} /> Back to reader
          </button>
        </div>

        {/* ---------- USERS ---------- */}
        <section className="flex flex-col gap-3" aria-label="Users">
          <h2 className={sectionTitle}>Users</h2>

          {/* Enroll form */}
          <form onSubmit={submitEnroll} className={`flex flex-wrap items-center gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
            <input
              type="email" required value={enrollEmail}
              onChange={(e) => setEnrollEmail(e.target.value)}
              placeholder="email (required)"
              aria-label="Enroll email"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[180px]`}
            />
            <input
              type="text" value={enrollName}
              onChange={(e) => setEnrollName(e.target.value)}
              placeholder="display name"
              aria-label="Enroll display name"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[120px]`}
            />
            <select
              value={enrollStatus} onChange={(e) => setEnrollStatus(e.target.value)}
              aria-label="Enroll status"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
            >
              <option value="pending">pending</option>
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
            <input
              type="number" min="0" value={enrollBudget}
              onChange={(e) => setEnrollBudget(e.target.value)}
              placeholder="budget (default)"
              aria-label="Enroll budget"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} w-32`}
            />
            <span className="flex items-center gap-2 text-[10px]">
              {CAPABILITIES.map((cap) => (
                <label key={cap} className="flex items-center gap-1 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enrollCaps[cap]}
                    onChange={() => toggleEnrollCap(cap)}
                    className="h-3 w-3 accent-blue-500"
                  />
                  {cap}
                </label>
              ))}
            </span>
            <button
              type="submit"
              disabled={enrolling}
              className="text-xs underline text-blue-500 flex items-center gap-1 disabled:opacity-60 disabled:no-underline"
            >
              {enrolling ? (<><Loader2 size={12} className="animate-spin" /> Enrolling…</>) : 'Enroll'}
            </button>
            {enrollNote && (
              <span className={`text-[10px] w-full ${enrollNote.kind === 'ok' ? 'text-green-600' : 'text-red-500'}`}>
                {enrollNote.text}
              </span>
            )}
          </form>

          {/* User rows */}
          {users === null ? (
            <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
          ) : users.map((u) => {
            const isSelf = u.id === currentUserId;
            const deleting = deleteTarget === u.id;
            return (
              <div key={u.id} data-testid={`user-row-${u.id}`} className={`flex flex-col gap-1 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate flex items-center gap-2">
                    <span className="truncate">{u.email}</span>
                    {u.oidc_sub === null && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 whitespace-nowrap">
                        awaiting first login
                      </span>
                    )}
                  </span>
                  <span className={`text-[10px] ${theme.textMuted} whitespace-nowrap`}>
                    {u.role}/{u.status}{u.created_at ? ` · ${String(u.created_at).slice(0, 10)}` : ''}
                  </span>
                  <span className="flex gap-2 shrink-0 items-center">
                    <BudgetField
                      key={`${u.id}-${u.inference_daily_token_budget ?? 'null'}`}
                      u={u} theme={theme} onSet={setBudget}
                    />
                    {u.status !== 'active' && (
                      <button onClick={() => patchUser(u.id, { status: 'active' })} className="text-[10px] text-green-600 underline">Activate</button>
                    )}
                    {u.status === 'active' && !isSelf && (
                      <button onClick={() => patchUser(u.id, { status: 'disabled' })} className="text-[10px] text-red-500 underline">Disable</button>
                    )}
                    {!isSelf && (
                      <button
                        onClick={() => { setDeleteTarget(deleting ? null : u.id); setDeleteTyped(''); }}
                        className="text-[10px] text-red-500 underline"
                      >{deleting ? 'Cancel' : 'Delete'}</button>
                    )}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-[10px]">
                  <span className={theme.textMuted}>capabilities:</span>
                  {CAPABILITIES.map((cap) => {
                    // Self-lockout guard: an admin cannot strip their own
                    // admin capability from their own row (server would
                    // allow it and lock them out of the console).
                    const locked = isSelf && cap === 'admin';
                    return (
                      <label
                        key={cap}
                        className={`flex items-center gap-1 ${locked ? 'opacity-50' : 'cursor-pointer'}`}
                      >
                        <input
                          type="checkbox"
                          checked={(u.capabilities ?? []).includes(cap)}
                          disabled={locked}
                          onChange={() => toggleCapability(u, cap, locked)}
                          className="h-3 w-3 accent-blue-500"
                        />
                        {cap}
                      </label>
                    );
                  })}
                </div>
                {deleting && (
                  <div className="flex flex-wrap items-center gap-2 text-[10px] pt-1">
                    <span className="text-red-500">
                      This permanently deletes this user's documents and chat history.
                    </span>
                    <input
                      type="text" value={deleteTyped}
                      onChange={(e) => setDeleteTyped(e.target.value)}
                      placeholder="type their email to confirm"
                      aria-label="Confirm email"
                      className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-44`}
                    />
                    <button
                      onClick={() => confirmDelete(u)}
                      disabled={deleteTyped !== u.email}
                      className="text-red-500 underline disabled:opacity-40 disabled:no-underline"
                    >Confirm delete</button>
                  </div>
                )}
              </div>
            );
          })}
        </section>

        {/* ---------- INFERENCE USAGE ---------- */}
        <section className="flex flex-col gap-3" aria-label="Inference usage">
          <div className="flex items-center justify-between">
            <h2 className={sectionTitle}>Inference usage</h2>
            <select
              value={days} onChange={(e) => setDays(Number(e.target.value))}
              aria-label="Usage days"
              className={`px-2 py-1 text-[10px] rounded border ${theme.border} ${theme.bgSecondary}`}
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
                  <th className="py-1 pr-2">Day (UTC)</th>
                  <th className="py-1 pr-2 text-right">Prompt</th>
                  <th className="py-1 pr-2 text-right">Eval</th>
                  <th className="py-1 pr-2 text-right">Requests</th>
                  <th className="py-1 text-right">Total tokens</th>
                </tr>
              </thead>
              <tbody>
                {usage.map((r, i) => (
                  // day is an ISO UTC date string — render as-is, never
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
        </section>

        {/* ---------- DEPLOYMENT CONFIG ---------- */}
        <section className="flex flex-col gap-3" aria-label="Deployment config">
          <h2 className={sectionTitle}>Deployment config</h2>
          {config === null ? (
            <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
          ) : (
            <div className={`flex flex-col gap-1 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary} text-xs`}>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Ollama URL</span><span className="truncate">{config.ollama_url}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Chat timeout</span><span>{config.timeout_s}s</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Allowed models</span><span className="text-right">{config.allowed_models ? config.allowed_models.join(', ') : 'all models allowed'}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Summarize model</span><span>{config.summarize_model ?? '—'}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Embed model</span><span>{config.embed_model ?? '—'}</span></div>
              <div className="flex justify-between gap-4"><span className={`${theme.textMuted}`}>Default daily budget</span><span>{fmtBudget(config.daily_token_budget)}</span></div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
