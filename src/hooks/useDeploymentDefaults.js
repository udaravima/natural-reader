import { useEffect, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';
import { resetPersisted } from './usePersistedState';
import { useLatest } from './useLatest';

// The built-in defaults, mirrored from server/services/user_defaults.py; used
// until the deployment's arrive, and when they can't be fetched.
export const BUILT_IN_DEFAULTS = {
    chatAutoTts: false,
    chatTtsMode: 'after-complete',
    think: 'off',
    selectedVoice: 'af_heart',
    playbackSpeed: 1.0,
};

const EPOCHS_KEY = 'neural-pdf-defaultEpochs';

/**
 * "Apply to everyone": for each setting whose epoch on the server is higher
 * than this browser last applied, drop the user's own choice so the default
 * shows again. `think` lives per model inside inferenceByModel, so it is
 * handed to `onResetThink` instead.
 */
export function applyEpochs(serverEpochs, { onResetThink } = {}) {
    let local = {};
    try { local = JSON.parse(localStorage.getItem(EPOCHS_KEY) || '{}') || {}; } catch { local = {}; }
    let changed = false;
    for (const [key, epoch] of Object.entries(serverEpochs || {})) {
        if (!Number.isInteger(epoch) || (local[key] || 0) >= epoch) continue;
        if (key === 'think') onResetThink?.();
        else resetPersisted(key);
        local[key] = epoch;
        changed = true;
    }
    if (changed) {
        try { localStorage.setItem(EPOCHS_KEY, JSON.stringify(local)); } catch { /* blocked */ }
    }
}

/** The deployment's defaults for preferences the user hasn't chosen. */
export function useDeploymentDefaults({ active, apiHost, apiPort, onResetThink }) {
    const [values, setValues] = useState(BUILT_IN_DEFAULTS);
    const onResetThinkRef = useLatest(onResetThink);

    useEffect(() => {
        if (!active) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const res = await apiFetch(apiHost, apiPort, '/v1/settings/defaults');
                if (!res.ok) return;
                const data = await res.json();
                if (cancelled) return;
                applyEpochs(data.epochs, { onResetThink: () => onResetThinkRef.current?.() });
                setValues({ ...BUILT_IN_DEFAULTS, ...(data.values || {}) });
            } catch { /* keep the built-in defaults */ }
        })();
        return () => { cancelled = true; };
    }, [active, apiHost, apiPort, onResetThinkRef]);

    return values;
}
