// TTS backend client. fetchImpl is injectable for tests; defaults to global fetch.
//
// Synthesis runs in the service worker (background.js), not the page: a request
// carrying an access token from an arbitrary site would need that site to pass
// the backend's CORS allowlist, and it never will. The worker's host permission
// skips CORS, and the token never enters the page.

export function base64ToBlob(b64, type = 'audio/wav') {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type });
}

function authHeaders(token) {
    return token ? { Authorization: `Bearer ${token}` } : {};
}

// A readable sentence for a failed response. FastAPI's detail is a string or,
// for 403s from the capability gate, an object.
async function errorMessage(res) {
    let detail = null;
    try { detail = (await res.json())?.detail ?? null; } catch { /* body not JSON */ }
    if (res.status === 401) {
        return 'not signed in: add an access token in the extension settings '
            + '(create one in Natural Reader under Settings → Account)';
    }
    if (detail && typeof detail === 'object') {
        if (detail.error === 'missing_capability') {
            return `your account lacks the "${detail.capability}" permission; ask an admin`;
        }
        if (detail.status) return `account ${detail.status}`;
        return detail.message || `HTTP ${res.status}`;
    }
    return detail || `HTTP ${res.status}`;
}

export async function health({ baseUrl, fetchImpl = globalThis.fetch } = {}) {
    try {
        const res = await fetchImpl(`${baseUrl}/v1/health`);
        if (!res.ok) return false;
        const data = await res.json();
        return data.status === 'ok' && data.model_loaded === true;
    } catch {
        return false;
    }
}

export async function synthesizeBase64({
    sentences, voice, speed, baseUrl, token, fetchImpl = globalThis.fetch, signal,
} = {}) {
    const res = await fetchImpl(`${baseUrl}/v1/batch_synthesize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ sentences, voice, speed }),
        signal,
    });
    if (!res.ok) throw new Error(`TTS request failed: ${await errorMessage(res)}`);
    const data = await res.json();
    return data.audio_base64;
}

export async function synthesize(opts = {}) {
    return base64ToBlob(await synthesizeBase64(opts));
}

// Can this backend + token read aloud? reason is one of: ok, unreachable,
// no_token, bad_token, account_inactive, no_reader. A single-user backend
// (sign-in off) answers /me without a token, so it is ok with none set.
export async function checkConnection({ baseUrl, token, fetchImpl = globalThis.fetch } = {}) {
    if (!(await health({ baseUrl, fetchImpl }))) return { ok: false, reason: 'unreachable' };
    let res;
    try {
        res = await fetchImpl(`${baseUrl}/v1/auth/me`, { headers: authHeaders(token) });
    } catch {
        return { ok: false, reason: 'unreachable' };
    }
    if (res.status === 401) return { ok: false, reason: token ? 'bad_token' : 'no_token' };
    if (res.status === 403) return { ok: false, reason: 'account_inactive' };
    if (!res.ok) return { ok: false, reason: 'unreachable' };
    const me = await res.json();
    if (!(me.capabilities || []).includes('reader')) return { ok: false, reason: 'no_reader' };
    return { ok: true, reason: 'ok', email: me.email };
}

export const CONNECTION_TEXT = {
    ok: 'Connected',
    unreachable: 'Server not reachable',
    no_token: 'Sign-in needed: paste an access token',
    bad_token: 'Token rejected (revoked, expired or mistyped)',
    account_inactive: 'Account is not active',
    no_reader: 'Account lacks the reader permission',
};
