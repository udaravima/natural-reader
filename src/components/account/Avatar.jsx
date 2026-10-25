import { useEffect, useState } from 'react';
import { apiFetch } from '../../utils/apiFetch';

// Fetched pictures, by URL. Each URL carries the picture's version (?v=), so
// a cached one is never stale; there are only a few per page.
const loaded = new Map();

function initials(name) {
    const parts = (name || '').replace(/^@/, '').split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] || '?') + (parts[1]?.[0] || '')).toUpperCase();
}

/**
 * A person's picture, or their initials while it loads or when they have
 * none. `url` is the API path from /v1/auth/me (avatar_url); it is fetched
 * with the app's credentials, so it also works when the API is on another
 * host (Settings → Connection), where a plain <img> would go unauthenticated.
 */
export default function Avatar({ apiHost, apiPort, url, name, size = 32, className = '' }) {
    // The picture fetched for `fetched.url`; a new url (a new upload) shows
    // the initials, not the old picture, until its own picture arrives.
    const [fetched, setFetched] = useState({ url: null, src: null });

    useEffect(() => {
        if (!url || loaded.has(url)) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const res = await apiFetch(apiHost, apiPort, url);
                if (!res.ok) return;
                const objectUrl = URL.createObjectURL(await res.blob());
                loaded.set(url, objectUrl);
                if (!cancelled) setFetched({ url, src: objectUrl });
            } catch {
                // Offline or refused: the initials stay.
            }
        })();
        return () => { cancelled = true; };
    }, [url, apiHost, apiPort]);

    const shown = !url ? null : loaded.get(url) ?? (fetched.url === url ? fetched.src : null);
    const box = { width: size, height: size };
    if (shown) {
        return <img src={shown} alt="" style={box} className={`rounded-full object-cover shrink-0 ${className}`} />;
    }
    return (
        <span aria-hidden="true" style={{ ...box, fontSize: Math.max(10, Math.round(size * 0.4)) }}
            className={`rounded-full shrink-0 flex items-center justify-center font-bold text-white bg-gradient-to-br from-blue-500 to-cyan-600 ${className}`}>
            {initials(name)}
        </span>
    );
}
