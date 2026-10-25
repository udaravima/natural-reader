import { useEffect, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';

/**
 * What this deployment lets the reader convert: the presets, OCR, picture
 * descriptions and page count the admin allows (GET /v1/settings/docling).
 * Fetched each time the convert dialog opens, so a change in the admin console
 * shows up without a reload. `null` until the server answers, and on any
 * failure: the dialog then behaves as it always did, and the server still
 * refuses what isn't allowed.
 */
export function useConvertLimits(apiHost, apiPort, open) {
    const [limits, setLimits] = useState(null);

    // Forget the last opening's answer the moment the dialog opens again, so a
    // stale one never shows while the new one is on its way.
    const [prevOpen, setPrevOpen] = useState(open);
    if (open !== prevOpen) {
        setPrevOpen(open);
        if (open) setLimits(null);
    }

    useEffect(() => {
        if (!open) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const res = await apiFetch(apiHost, apiPort, '/v1/settings/docling');
                if (!res.ok) return;
                const data = await res.json();
                if (!cancelled) setLimits(data);
            } catch { /* keep null: the dialog behaves as before */ }
        })();
        return () => { cancelled = true; };
    }, [open, apiHost, apiPort]);

    return limits;
}
