import { describe, it, expect } from 'vitest';
import { DEFAULTS, getSettings, setSettings } from './settings.js';
import { VOICES, DEFAULT_VOICE } from './voices.js';

function fakeStorage(initial = {}) {
    const store = { ...initial };
    return {
        get: async (keys) => {
            const out = {};
            for (const k of keys) if (k in store) out[k] = store[k];
            return out;
        },
        set: async (patch) => { Object.assign(store, patch); },
        _store: store,
    };
}

describe('settings', () => {
    it('returns defaults when storage is empty', async () => {
        expect(await getSettings(fakeStorage())).toEqual(DEFAULTS);
    });
    it('merges stored values over defaults', async () => {
        const s = fakeStorage({ voice: 'am_michael' });
        expect(await getSettings(s)).toEqual({ ...DEFAULTS, voice: 'am_michael' });
    });
    it('persists a patch and returns the merged result', async () => {
        const s = fakeStorage();
        const result = await setSettings({ speed: 1.5 }, s);
        expect(result.speed).toBe(1.5);
        expect(s._store.speed).toBe(1.5);
    });
});

describe('voices', () => {
    it('has 27 well-formed entries', () => {
        expect(VOICES).toHaveLength(27);
        for (const v of VOICES) {
            expect(typeof v.id).toBe('string');
            expect(typeof v.name).toBe('string');
        }
    });
    it('default voice exists in the list and matches settings default', () => {
        expect(VOICES.some((v) => v.id === DEFAULT_VOICE)).toBe(true);
        expect(DEFAULTS.voice).toBe(DEFAULT_VOICE);
    });
});
