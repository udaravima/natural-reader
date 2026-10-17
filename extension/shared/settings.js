import { DEFAULT_VOICE } from './voices.js';

export const DEFAULTS = { voice: DEFAULT_VOICE, speed: 1.0, baseUrl: 'http://localhost:8000' };

export async function getSettings(storage) {
    const store = storage || chrome.storage.sync;
    const stored = await store.get(Object.keys(DEFAULTS));
    return { ...DEFAULTS, ...stored };
}

export async function setSettings(patch, storage) {
    const store = storage || chrome.storage.sync;
    await store.set(patch);
    return getSettings(store);
}

// The access token lives in chrome.storage.local, apart from the settings
// above: storage.sync would copy a secret to every browser signed in to the
// same Google account. '' means no token (a single-user backend needs none).
export async function getToken(storage) {
    const store = storage || chrome.storage.local;
    const { token } = await store.get(['token']);
    return token || '';
}

export async function setToken(value, storage) {
    const store = storage || chrome.storage.local;
    await store.set({ token: String(value || '').trim() });
}

// Host-permission pattern for a backend URL. Match patterns carry no port,
// so http://localhost:8000 becomes http://localhost/*.
export function originPattern(url) {
    try {
        const u = new URL(url);
        if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
        return `${u.protocol}//${u.hostname}/*`;
    } catch {
        return null;
    }
}
