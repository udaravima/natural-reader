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
