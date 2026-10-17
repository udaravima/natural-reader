import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Loader2, Monitor } from 'lucide-react';
import { apiFetch } from '../../utils/apiFetch';
import { squareImage } from '../../lib/squareImage';
import { describeUserAgent } from '../../lib/userAgent';
import Avatar from './Avatar';

const ago = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

function sinceLabel(iso) {
    const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
    const steps = [[60, 'second'], [3600, 'minute'], [86400, 'hour'], [Infinity, 'day']];
    const divisors = { second: 1, minute: 60, hour: 3600, day: 86400 };
    const [, unit] = steps.find(([limit]) => Math.abs(seconds) < limit);
    if (unit === 'second') return 'just now';
    return ago.format(Math.round(seconds / divisors[unit]), unit);
}

function dateLabel(iso) {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

async function failure(res) {
    try {
        const d = (await res.json())?.detail;
        if (typeof d === 'string') return d;
        if (d?.message) return d.message;
    } catch { /* not JSON */ }
    return `HTTP ${res.status}`;
}

/**
 * Settings → Profile: your picture, where your name and password live, and
 * the browsers signed in to your account. The name and email come from the
 * sign-in provider at every sign-in, so they are changed there
 * (user.account_url, null when the server knows no such page).
 */
export function ProfilePanel({ theme, apiHost, apiPort, user, onUserChanged, showToast }) {
    const fileRef = useRef(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [browsers, setBrowsers] = useState(null);   // null: loading
    const [browsersFailed, setBrowsersFailed] = useState(false);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const res = await apiFetch(apiHost, apiPort, '/v1/auth/sessions');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const list = await res.json();
                if (!cancelled) setBrowsers(list);
            } catch {
                if (!cancelled) setBrowsersFailed(true);
            }
        })();
        return () => { cancelled = true; };
    }, [apiHost, apiPort]);

    const choosePicture = async (e) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        setBusy(true);
        setError(null);
        try {
            let blob;
            try {
                blob = await squareImage(file);
            } catch {
                throw new Error("That file isn't a picture this browser can open.");
            }
            const res = await apiFetch(apiHost, apiPort, '/v1/auth/me/avatar', {
                method: 'PUT', headers: { 'Content-Type': blob.type }, body: blob,
            });
            if (!res.ok) throw new Error(await failure(res));
            onUserChanged?.({ avatar_url: (await res.json()).avatar_url });
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    const removePicture = async () => {
        setBusy(true);
        setError(null);
        try {
            const res = await apiFetch(apiHost, apiPort, '/v1/auth/me/avatar', { method: 'DELETE' });
            if (!res.ok) throw new Error(await failure(res));
            onUserChanged?.({ avatar_url: null });
        } catch (err) {
            setError(err.message);
        } finally {
            setBusy(false);
        }
    };

    const signOut = async (sid) => {
        const res = await apiFetch(apiHost, apiPort, `/v1/auth/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' });
        if (res.ok || res.status === 404) setBrowsers((list) => list.filter((b) => b.id !== sid));
        else showToast?.(`Couldn't sign it out: ${await failure(res)}`, 5000);
    };

    const signOutOthers = async () => {
        const res = await apiFetch(apiHost, apiPort, '/v1/auth/sessions/sign-out-others', { method: 'POST' });
        if (!res.ok) { showToast?.(`Couldn't sign them out: ${await failure(res)}`, 5000); return; }
        setBrowsers((list) => list.filter((b) => b.current));
    };

    const name = user?.name || user?.email || 'You';
    const others = (browsers || []).filter((b) => !b.current);
    const linkBtn = 'text-xs underline text-blue-500 disabled:opacity-40 disabled:no-underline';

    return (
        <>
            <section aria-label="Picture" className="flex items-center gap-4">
                <Avatar apiHost={apiHost} apiPort={apiPort} url={user?.avatar_url} name={name} size={72} />
                <div className="flex flex-col gap-1 min-w-0">
                    <p className={`text-sm font-bold truncate ${theme.text}`}>{name}</p>
                    {user?.email && <p className={`text-xs truncate ${theme.textMuted}`}>{user.email}</p>}
                    <div className="flex items-center gap-3 mt-1">
                        <button onClick={() => fileRef.current?.click()} disabled={busy} className={linkBtn}>
                            {user?.avatar_url ? 'Change picture' : 'Add a picture'}
                        </button>
                        {user?.avatar_url && (
                            <button onClick={removePicture} disabled={busy} className={linkBtn}>Remove</button>
                        )}
                        {busy && <Loader2 size={12} className="animate-spin" />}
                    </div>
                    <input ref={fileRef} type="file" accept="image/*" className="hidden"
                        aria-label="Picture file" onChange={choosePicture} />
                    {error && <p role="alert" className="text-[11px] text-red-500">{error}</p>}
                </div>
            </section>

            <section aria-label="Sign-in account" className={`flex flex-col gap-1.5 p-3 rounded-xl border ${theme.border} ${theme.bgSecondary}`}>
                <p className={`text-xs font-bold ${theme.text}`}>Name, email, password and two-step sign-in</p>
                <p className={`text-[11px] ${theme.textSecondary}`}>
                    These belong to the account you sign in with, and your name and email here follow it
                    each time you sign in.
                </p>
                {user?.account_url ? (
                    <a href={user.account_url} target="_blank" rel="noopener noreferrer"
                        className="self-start flex items-center gap-1 text-xs font-bold text-blue-500 underline">
                        Manage your account <ExternalLink size={11} />
                    </a>
                ) : (
                    <p className={`text-[11px] ${theme.textMuted}`}>Ask your administrator to change them.</p>
                )}
            </section>

            <section aria-label="Signed-in browsers" className="flex flex-col gap-2">
                <h3 className={`text-xs font-bold uppercase tracking-wider ${theme.textMuted}`}>Signed-in browsers</h3>
                {browsersFailed ? (
                    <p className="text-xs text-red-500">Couldn&apos;t load the list.</p>
                ) : browsers === null ? (
                    <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
                ) : (
                    <>
                        <ul className="flex flex-col gap-1.5">
                            {browsers.map((b) => (
                                <li key={b.id} className={`flex items-center gap-3 p-2 rounded-lg border ${theme.border}`}>
                                    <Monitor size={16} className={`shrink-0 ${theme.textSecondary}`} />
                                    <div className="flex-1 min-w-0">
                                        <p className={`text-xs font-bold truncate ${theme.text}`}>
                                            {describeUserAgent(b.user_agent)}
                                            {b.current && <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-500">this browser</span>}
                                        </p>
                                        <p className={`text-[10px] ${theme.textMuted}`}>
                                            {b.current ? 'In use now' : `Last used ${sinceLabel(b.last_seen_at)}`} · signed in {dateLabel(b.created_at)}
                                        </p>
                                    </div>
                                    {!b.current && (
                                        <button onClick={() => signOut(b.id)} className="text-[11px] underline text-red-500">Sign out</button>
                                    )}
                                </li>
                            ))}
                        </ul>
                        {browsers.length === 0 && (
                            <p className={`text-xs ${theme.textMuted}`}>No browser is signed in with a session (sign-in may be off on this server).</p>
                        )}
                        {others.length > 0 && (
                            <button onClick={signOutOthers} className="self-start text-xs underline text-red-500 font-bold">
                                Sign out everywhere else
                            </button>
                        )}
                        <p className={`text-[10px] ${theme.textMuted}`}>
                            This ends the browser&apos;s session here. A browser still signed in to your sign-in
                            account can come back without a password until that ends too
                            {user?.account_url ? ': end it under Manage your account.' : '.'}
                        </p>
                    </>
                )}
            </section>
        </>
    );
}
