import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';
import { HouseRulesEditor } from './HouseRulesEditor';
import { ProfileEditor } from './ProfileEditor';

/**
 * Admin console: the assistant (profile library spec, 2026-11-16). House rules lead every chat; then the profile
 * the chat uses, one of the named profiles here; the app's own rules about documents and tools come last and win
 * where they conflict. People pick a profile per chat from the ones that are on, by name and description; the
 * default is what a chat gets until someone picks. The server cleans, caps and warns; this only edits.
 */

const BASE = '/v1/admin/assistant';
const plural = (n, one, many) => (n === 1 ? one : many);

export function AssistantProfileSection({ theme, apiHost, apiPort, showToast, refreshKey = 0, onDirtyChange }) {
  const [data, setData] = useState(null);         // the server's view; null while loading
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [helperOn, setHelperOn] = useState(null);  // null until the server says
  const [editing, setEditing] = useState(null);    // the profile in the editor ({ id: null } for a new one), or null
  const [houseDirty, setHouseDirty] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiHost, apiPort, `${BASE}/profiles`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [apiHost, apiPort]);

  useEffect(() => { load(); }, [load]);

  // Whether a helper model is set up. `refreshKey` changes when a setting was saved elsewhere in the console.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, '/v1/settings/helper');
        if (!cancelled) setHelperOn(res.ok ? (await res.json()).on === true : false);
      } catch {
        if (!cancelled) setHelperOn(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiHost, apiPort, refreshKey]);

  const dirty = houseDirty || (editing !== null && editorDirty);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  /** Send one change; take the view it returns. Resolves to the body, or null after saying why it failed. */
  const send = async (method, path, body, failure) => {
    setBusy(true);
    try {
      const res = await apiFetch(apiHost, apiPort, path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast?.(out?.detail?.message || `${failure} (HTTP ${res.status}).`, 5000);
        return null;
      }
      setData(out);
      return out;
    } catch {
      showToast?.('The server could not be reached.', 5000);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const saveHouse = async (text) => {
    if (await send('PUT', `${BASE}/house-rules`, { text }, 'Saving the house rules failed')) {
      showToast?.(text.trim() ? 'House rules saved: they apply from the next message.' : 'House rules removed.', 4000);
    }
  };

  const openEditor = (target) => {
    if (editing && editorDirty && !window.confirm('Discard the unsaved changes to this profile?')) return;
    setEditorDirty(false);
    setEditing(target);
  };

  const closeEditor = () => { setEditorDirty(false); setEditing(null); };

  const saveProfile = async (values) => {
    const ok = editing.id
      ? await send('PUT', `${BASE}/profiles/${editing.id}`, values, 'Saving the profile failed')
      : await send('POST', `${BASE}/profiles`, values, 'Saving the profile failed');
    if (ok) {
      closeEditor();
      showToast?.('Profile saved: it applies from the next message.', 4000);
    }
  };

  const change = (p, body) => send('PUT', `${BASE}/profiles/${p.id}`, body, 'Changing the profile failed');

  const move = (i, by) => {
    const ids = data.profiles.map((p) => p.id);
    [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
    send('PUT', `${BASE}/profiles/order`, { ids }, 'Moving the profile failed');
  };

  const remove = async (p) => {
    const uses = p.chats
      ? ` ${p.chats} ${plural(p.chats, 'chat uses it; it', 'chats use it; they')} will use the default from ${plural(p.chats, 'its', 'their')} next message.`
      : '';
    if (!window.confirm(`Delete the profile "${p.name}"?${uses}`)) return;
    const out = await send('DELETE', `${BASE}/profiles/${p.id}`, undefined, 'Deleting the profile failed');
    if (!out) return;
    if (editing?.id === p.id) closeEditor();
    showToast?.(out.chats
      ? `Profile deleted. ${out.chats} ${plural(out.chats, 'chat', 'chats')} will use the default from their next message.`
      : 'Profile deleted.', 5000);
  };

  const sectionTitle = 'text-xs font-bold uppercase tracking-wider text-blue-500';
  const link = 'min-h-11 md:min-h-0 underline text-blue-500 disabled:opacity-40 disabled:no-underline';
  const badge = `text-[10px] px-1.5 py-0.5 rounded border ${theme.border} ${theme.textMuted}`;
  const profiles = data?.profiles ?? [];
  const own = profiles.filter((p) => !p.builtin);   // the ones an admin wrote; The Librarian is always there
  const house = data?.houseRules?.text ?? '';

  return (
    <section className="flex flex-col gap-4" aria-label="Assistant">
      <h2 className={sectionTitle}>Assistant</h2>
      {failed ? (
        <p className="text-xs text-red-500">Couldn&apos;t load the assistant profiles.</p>
      ) : data === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : (
        <>
          <p className={`text-xs ${theme.textSecondary}`}>
            Every chat starts with the house rules, then the profile it uses. The app&apos;s own rules about documents
            and tools follow and take precedence. People pick a profile for each chat from the ones that are on; a chat
            nobody picked for gets the default, and keeps its profile when the default changes.
          </p>
          <HouseRulesEditor key={house} theme={theme} saved={house} maxChars={data.houseRules.maxChars} busy={busy}
            onSave={saveHouse} onDirtyChange={setHouseDirty} />

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-xs font-bold">Profiles</h3>
              <button onClick={() => openEditor({ id: null, name: '', description: '', warnings: [],
                text: !own.length && data.file.inUse ? data.file.text : '' })}
                disabled={busy} className={`text-xs font-bold ${link}`}>
                Add profile
              </button>
            </div>
            {!own.length && (
              <p className={`text-xs ${theme.textMuted}`}>
                {data.file.inUse
                  ? 'The deployment file is the profile until you add one. Add profile starts from its text.'
                  : "No profiles yet: chats get the house rules and the app's own rules. Add one to give the assistant a name, a voice and its own rules."}
              </p>
            )}
            {own.length > 1 && !house.trim() && (
              <p className="text-xs text-amber-600">
                House rules are empty, and each profile is sent on its own: rules every profile needs belong in House
                rules.
              </p>
            )}
            {profiles.length > 0 && (
              <ul className="flex flex-col gap-2" aria-label="Profiles">
                {profiles.map((p, i) => (
                  <li key={p.id} aria-label={p.name} className={`flex flex-col gap-1 p-2 rounded border ${theme.border}`}>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-bold">{p.name}</span>
                      {p.builtin && <span className={badge}>Built in</span>}
                      {p.isDefault && <span className={badge}>Default</span>}
                      {!p.enabled && <span className={badge}>Off</span>}
                      {p.chats > 0 && (
                        <span className={theme.textMuted}>Used in {p.chats} {plural(p.chats, 'chat', 'chats')}</span>
                      )}
                    </div>
                    {p.description && <p className={`text-xs ${theme.textSecondary}`}>{p.description}</p>}
                    {p.builtin && (
                      <p className={`text-xs ${theme.textMuted}`}>
                        The app&apos;s own document assistant, always here: it can be renamed, not deleted or turned off.
                      </p>
                    )}
                    {(p.warnings || []).map((w) => <p key={w} className="text-xs text-amber-600">{w}</p>)}
                    <div className="flex flex-wrap items-center gap-x-3 text-xs">
                      <button onClick={() => openEditor(p)} disabled={busy} aria-label={`Edit ${p.name}`} className={link}>
                        Edit
                      </button>
                      {!p.isDefault && p.enabled && (
                        <button onClick={() => change(p, { isDefault: true })} disabled={busy}
                          aria-label={`Make ${p.name} the default`} className={link}>
                          Make default
                        </button>
                      )}
                      {!p.isDefault && !p.builtin && (
                        <button onClick={() => change(p, { enabled: !p.enabled })} disabled={busy}
                          aria-label={`${p.enabled ? 'Turn off' : 'Turn on'} ${p.name}`} className={link}>
                          {p.enabled ? 'Turn off' : 'Turn on'}
                        </button>
                      )}
                      <button onClick={() => move(i, -1)} disabled={busy || i === 0}
                        aria-label={`Move ${p.name} up`} className={link}>
                        Up
                      </button>
                      <button onClick={() => move(i, 1)} disabled={busy || i === profiles.length - 1}
                        aria-label={`Move ${p.name} down`} className={link}>
                        Down
                      </button>
                      {!p.builtin && (!p.isDefault || profiles.length === 1) && (
                        <button onClick={() => remove(p)} disabled={busy} aria-label={`Delete ${p.name}`}
                          className={`${link} text-red-500`}>
                          Delete
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {editing && (
            <ProfileEditor key={editing.id ?? 'new'} theme={theme} apiHost={apiHost} apiPort={apiPort}
              showToast={showToast} initial={editing} houseText={house} helperOn={helperOn} busy={busy}
              refreshKey={refreshKey} onSave={saveProfile} onCancel={closeEditor} onDirtyChange={setEditorDirty}
              builtin={Boolean(editing.builtin)}
              limits={{ name: data.nameMaxChars, description: data.descriptionMaxChars, text: data.maxChars,
                backstory: data.backstoryMaxChars }} />
          )}
        </>
      )}
    </section>
  );
}
