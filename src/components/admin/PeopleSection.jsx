import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '../../utils/apiFetch';
import { formatDay } from '../../lib/zoneFormat';

/**
 * People: the user list, the enroll form, per-user budget / project limit /
 * capabilities, and delete. Moved out of AdminConsole unchanged.
 */

const CAPABILITIES = ['reader', 'chat', 'admin', 'docling'];

const PAGE = 50;               // rows per request; the server's default page too
const MAX_PAGE = 200;          // the server's ceiling for `limit`
const SEARCH_DELAY_MS = 300;   // typing pauses this long before a search is sent

// How many people match, before paging: the server's X-Total-Count. A server
// (or a mock) that sends none is taken to have sent everything.
function totalOf(res, rows) {
  const header = res.headers?.get?.('X-Total-Count');
  const n = header == null ? NaN : Number(header);
  return Number.isFinite(n) ? n : rows.length;
}

// A capability is a Keycloak realm role; a realm imported before a role was
// added to the export lacks it, and the app can't create roles (no
// manage-realm), so say which one to create. null for any other refusal.
function roleMissingMessage(body) {
  const detail = body?.detail;
  if (detail?.reason !== 'kc_role_missing') return null;
  return `Keycloak has no “${detail.role}” role yet. Create it in the Keycloak admin console (Realm roles → Create role, name: ${detail.role}), then try again.`;
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
        className={`min-h-11 md:min-h-0 px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-20`}
      />
      <span aria-hidden="true" className={`text-[10px] ${theme.textMuted}`}>tokens/day</span>
      <button onClick={() => onSet(u, val)} className="min-h-11 md:min-h-0 text-[10px] underline">Set</button>
    </span>
  );
}

// Per-user project limit (A0 §5). Same semantics as the budget: empty =
// deployment default (PROJECT_LIMIT_PER_USER), 0 = unlimited.
function ProjectLimitField({ u, theme, onSet }) {
  const [val, setVal] = useState(u.project_limit == null ? '' : String(u.project_limit));
  return (
    <span className="flex items-center gap-1 shrink-0">
      <input
        type="number" min="0" value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder="default"
        aria-label={`Project limit for ${u.email}`}
        title="projects this person may create; empty = default, 0 = unlimited"
        className={`min-h-11 md:min-h-0 px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-16`}
      />
      <span aria-hidden="true" className={`text-[10px] ${theme.textMuted}`}>projects</span>
      <button onClick={() => onSet(u, val)} aria-label={`Set project limit for ${u.email}`} className="min-h-11 md:min-h-0 text-[10px] underline">Set</button>
    </span>
  );
}

export function PeopleSection({ theme, apiHost, apiPort, currentUserId, showToast, zone = 'UTC' }) {
  const [users, setUsers] = useState(null);

  // Search. `query` is what the box shows; `term` follows it 300 ms after typing stops,
  // and is what is sent. Status and capability apply at once.
  const [query, setQuery] = useState('');
  const [term, setTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [capFilter, setCapFilter] = useState('');
  const [total, setTotal] = useState(0);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Sequence number of the newest list request. An answer carrying an older number is
  // dropped, so a slow reply to "ad" can never overwrite the list for "ada".
  const latest = useRef(0);
  const loadedCount = useRef(0);   // rows on screen, so a reload after an edit keeps them

  // Enroll form
  const [enrollEmail, setEnrollEmail] = useState('');
  const [enrollName, setEnrollName] = useState('');
  const [enrollUsername, setEnrollUsername] = useState('');
  const [enrollFirst, setEnrollFirst] = useState('');
  const [enrollLast, setEnrollLast] = useState('');
  const [enrollStatus, setEnrollStatus] = useState('pending');
  const [enrollBudget, setEnrollBudget] = useState('');
  const [enrollCaps, setEnrollCaps] = useState({ reader: false, chat: false, admin: false, docling: false });
  const [enrollNote, setEnrollNote] = useState(null); // { kind: 'ok'|'err', text }
  const [enrolling, setEnrolling] = useState(false); // POST in flight → busy button

  // Delete flow
  const [deleteTarget, setDeleteTarget] = useState(null); // user id
  const [deleteTyped, setDeleteTyped] = useState('');

  useEffect(() => {
    const t = setTimeout(() => setTerm(query.trim()), SEARCH_DELAY_MS);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => { loadedCount.current = users?.length ?? 0; }, [users]);

  const pageUrl = useCallback((offset, limit) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (term) params.set('q', term);
    if (statusFilter) params.set('status', statusFilter);
    if (capFilter) params.set('capability', capFilter);
    return `/v1/admin/users?${params}`;
  }, [term, statusFilter, capFilter]);

  // Loads from the top with the current search. After an edit (fresh = false) it asks for as
  // many rows as are on screen, so pages opened with Show more don't collapse back to 50.
  const loadUsers = useCallback(async (fresh = false) => {
    const mine = ++latest.current;
    try {
      const limit = fresh ? PAGE : Math.min(MAX_PAGE, Math.max(PAGE, loadedCount.current));
      const res = await apiFetch(apiHost, apiPort, pageUrl(0, limit));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const rows = await res.json();
      if (mine !== latest.current) return;
      setUsers(rows);
      setTotal(totalOf(res, rows));
      setLoadFailed(false);
    } catch (e) {
      if (mine !== latest.current) return;
      setUsers([]);
      setTotal(0);
      setLoadFailed(true);
      showToast(`Could not load users: ${e.message}`, 5000);
    }
  }, [apiHost, apiPort, showToast, pageUrl]);

  // Runs on mount and whenever the search changes.
  useEffect(() => { loadUsers(true); }, [loadUsers]);

  const showMore = async () => {
    const mine = latest.current;
    setLoadingMore(true);
    try {
      const res = await apiFetch(apiHost, apiPort, pageUrl(users.length, PAGE));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const rows = await res.json();
      if (mine !== latest.current) return;   // the search changed while this was in flight
      setUsers((prev) => {
        const have = new Set(prev.map((u) => u.id));
        return [...prev, ...rows.filter((u) => !have.has(u.id))];
      });
      setTotal(totalOf(res, rows));
    } catch (e) {
      if (mine === latest.current) showToast(`Could not load more: ${e.message}`, 5000);
    } finally {
      setLoadingMore(false);
    }
  };

  const patchUser = async (id, body) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/admin/users/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.status === 409) {
        const missing = roleMissingMessage(await res.json().catch(() => null));
        if (missing) { showToast(missing, 8000); return; }
      }
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
    if (enrollUsername.trim()) body.username = enrollUsername.trim();
    if (enrollFirst.trim()) body.first_name = enrollFirst.trim();
    if (enrollLast.trim()) body.last_name = enrollLast.trim();
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
        const missing = roleMissingMessage(await res.json().catch(() => null));
        setEnrollNote({ kind: 'err', text: missing ?? 'That email is already taken.' });
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
      setEnrollUsername(''); setEnrollFirst(''); setEnrollLast('');
      setEnrollStatus('pending');
      setEnrollCaps({ reader: false, chat: false, admin: false, docling: false });
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

  const setProjectLimit = async (u, raw) => {
    const trimmed = raw.trim();
    const value = trimmed === '' ? null : Number(trimmed);
    if (trimmed !== '' && (!Number.isInteger(value) || value < 0)) {
      showToast('Project limit must be a whole number ≥ 0 (empty = default, 0 = unlimited).', 4000);
      return;
    }
    await patchUser(u.id, { project_limit: value });
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
      showToast(`Deleted ${u.email} — their library and chat history were removed; projects they belonged to stay (ownerless ones appear under Projects).`, 6000);
      await loadUsers();
    } catch (e) {
      showToast(`Delete failed: ${e.message}`, 5000);
    }
  };

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';

  return (
      <section className="flex flex-col gap-3" aria-label="Users">
        <h2 className={sectionTitle}>Users</h2>

        {/* Enroll form */}
        <form onSubmit={submitEnroll} className={`flex flex-wrap items-center gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
          <input
            type="email" required value={enrollEmail}
            onChange={(e) => setEnrollEmail(e.target.value)}
            placeholder="email (required)"
            aria-label="Enroll email"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[180px]`}
          />
          <input
            type="text" value={enrollName}
            onChange={(e) => setEnrollName(e.target.value)}
            placeholder="display name"
            aria-label="Enroll display name"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[120px]`}
          />
          <input type="text" value={enrollUsername} onChange={(e) => setEnrollUsername(e.target.value)}
            placeholder="username" aria-label="Enroll username"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[100px]`} />
          <input type="text" value={enrollFirst} onChange={(e) => setEnrollFirst(e.target.value)}
            placeholder="first name" aria-label="Enroll first name"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[100px]`} />
          <input type="text" value={enrollLast} onChange={(e) => setEnrollLast(e.target.value)}
            placeholder="last name" aria-label="Enroll last name"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[100px]`} />
          <select
            value={enrollStatus} onChange={(e) => setEnrollStatus(e.target.value)}
            aria-label="Enroll status"
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
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
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} w-32`}
          />
          <span className="flex items-center gap-2 text-[10px]">
            {CAPABILITIES.map((cap) => (
              <label key={cap} className="flex items-center gap-1 cursor-pointer min-h-11 md:min-h-0">
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
            className="min-h-11 md:min-h-0 text-xs underline text-blue-500 flex items-center gap-1 disabled:opacity-60 disabled:no-underline"
          >
            {enrolling ? (<><Loader2 size={12} className="animate-spin" /> Enrolling…</>) : 'Enroll'}
          </button>
          {enrollNote && (
            <span className={`text-[10px] w-full ${enrollNote.kind === 'ok' ? 'text-green-600' : 'text-red-500'}`}>
              {enrollNote.text}
            </span>
          )}
        </form>

        {/* Search and filters */}
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search" value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Name, email or username"
            aria-label="Search people"
            maxLength={200}
            className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[200px] flex-1`}
          />
          <label className="flex items-center gap-1 text-[10px]">
            <span className={theme.textMuted}>Status</span>
            <select
              value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
              className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="pending">Pending</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <label className="flex items-center gap-1 text-[10px]">
            <span className={theme.textMuted}>Capability</span>
            <select
              value={capFilter} onChange={(e) => setCapFilter(e.target.value)}
              className={`min-h-11 md:min-h-0 px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
            >
              <option value="">Any</option>
              {CAPABILITIES.map((cap) => <option key={cap} value={cap}>{cap}</option>)}
            </select>
          </label>
        </div>
        {users !== null && (
          <p role="status" className={`text-[10px] ${theme.textMuted}`}>Showing {users.length} of {total}</p>
        )}

        {/* User rows */}
        {users === null ? (
          <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
        ) : users.length === 0 && !loadFailed ? (
          <p className={`text-xs ${theme.textMuted}`}>No one matches.</p>
        ) : users.map((u) => {
          const isSelf = u.id === currentUserId;
          const deleting = deleteTarget === u.id;
          return (
            <div key={u.id} data-testid={`user-row-${u.id}`} className={`flex flex-col gap-1 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="truncate flex items-center gap-2 max-w-full">
                  <span className="truncate">{u.email}</span>
                  {u.oidc_sub === null && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 whitespace-nowrap">
                      awaiting first login
                    </span>
                  )}
                </span>
                <span className={`text-[10px] ${theme.textMuted} whitespace-nowrap`}>
                  {u.role}/{u.status}{u.created_at ? ` · ${formatDay(u.created_at, zone)}` : ''}
                </span>
                <span className="flex flex-wrap gap-2 items-center">
                  <BudgetField
                    key={`${u.id}-${u.inference_daily_token_budget ?? 'null'}`}
                    u={u} theme={theme} onSet={setBudget}
                  />
                  <ProjectLimitField
                    key={`${u.id}-pl-${u.project_limit ?? 'null'}`}
                    u={u} theme={theme} onSet={setProjectLimit}
                  />
                  {u.status !== 'active' && (
                    <button onClick={() => patchUser(u.id, { status: 'active' })} className="min-h-11 md:min-h-0 text-[10px] text-green-600 underline">Activate</button>
                  )}
                  {u.status === 'active' && !isSelf && (
                    <button onClick={() => patchUser(u.id, { status: 'disabled' })} className="min-h-11 md:min-h-0 text-[10px] text-red-500 underline">Disable</button>
                  )}
                  {!isSelf && (
                    <button
                      onClick={() => { setDeleteTarget(deleting ? null : u.id); setDeleteTyped(''); }}
                      className="min-h-11 md:min-h-0 text-[10px] text-red-500 underline"
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
                      className={`min-h-11 md:min-h-0 flex items-center gap-1 ${locked ? 'opacity-50' : 'cursor-pointer'}`}
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
                    This permanently deletes their library and chat history. Projects they belong to stay; any they were the only Owner of become ownerless (recover them under Projects).
                  </span>
                  <input
                    type="text" value={deleteTyped}
                    onChange={(e) => setDeleteTyped(e.target.value)}
                    placeholder="type their email to confirm"
                    aria-label="Confirm email"
                    className={`min-h-11 md:min-h-0 px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-44`}
                  />
                  <button
                    onClick={() => confirmDelete(u)}
                    disabled={deleteTyped !== u.email}
                    className="min-h-11 md:min-h-0 text-red-500 underline disabled:opacity-40 disabled:no-underline"
                  >Confirm delete</button>
                </div>
              )}
            </div>
          );
        })}

        {users !== null && users.length < total && (
          <button
            onClick={showMore} disabled={loadingMore}
            className="min-h-11 md:min-h-0 self-start text-xs underline text-blue-500 flex items-center gap-1 disabled:opacity-60 disabled:no-underline"
          >
            {loadingMore ? (<><Loader2 size={12} className="animate-spin" /> Loading…</>) : 'Show more'}
          </button>
        )}
      </section>
  );
}
