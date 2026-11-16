// What this deployment calls the app (server/services/app_identity.py: APP_NAME, APP_TAGLINE, APP_COPYRIGHT).
// The server is the one place they are set; the page reads them before sign-in, because the sign-in screen
// shows them. Until that answer arrives, and whenever it can't be read, the built-in name stands.
import { useEffect, useState } from 'react';
import { version } from '../../package.json';
import { buildApiUrl } from '../utils/url';

export const APP_VERSION = version;

export const DEFAULT_IDENTITY = Object.freeze({
    name: 'Natural Reader',
    tagline: 'an app for reading and listening to documents',
    copyright: '',
});

const text = (v, fallback) => (typeof v === 'string' && v.trim() ? v.trim() : fallback);

/** Read the identity and name the browser tab after it. Never rejects. */
export async function loadAppIdentity(host, port) {
    let identity = DEFAULT_IDENTITY;
    try {
        const res = await fetch(buildApiUrl(host, port, '/v1/app'));
        const body = res.ok ? await res.json() : null;
        if (body && typeof body.name === 'string' && body.name.trim()) {
            identity = {
                name: body.name.trim(),
                tagline: text(body.tagline, DEFAULT_IDENTITY.tagline),
                copyright: text(body.copyright, ''),
            };
        }
    } catch { /* the built-in name stands */ }
    document.title = identity.name;
    return identity;
}

/** "Acme Docs 2.12.0 · © 2026 Example Org": which release is running, and the deployment's copyright if it set one. */
export function footLine(identity, appVersion = APP_VERSION) {
    const lead = `${identity.name} ${appVersion}`;
    return identity.copyright ? `${lead} · ${identity.copyright}` : lead;
}

export function useAppIdentity(host, port) {
    const [identity, setIdentity] = useState(DEFAULT_IDENTITY);
    useEffect(() => {
        let live = true;
        loadAppIdentity(host, port).then((got) => { if (live) setIdentity(got); });
        return () => { live = false; };
    }, [host, port]);
    return identity;
}
