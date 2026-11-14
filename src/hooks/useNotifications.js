import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchUnread } from '../lib/notifications';

export const POLL_MS = 60_000;

/**
 * The bell's counts: unread rows and how many of them need action (spec 2026-11-06, Delivery). Asks at once, then
 * every 60 s while the page is visible; nothing while the tab is hidden (it asks again the moment it shows), and
 * nothing at all while `enabled` is false (signed out). `refresh()` asks now: after the page's own Index, Convert or
 * Retry finishes, and after the panel marks something read. A failed request keeps the last counts.
 */
export function useNotifications(apiHost, apiPort, enabled) {
    const [counts, setCounts] = useState({ count: 0, action: 0 });
    const alive = useRef(true);

    const refresh = useCallback(async () => {
        const got = await fetchUnread(apiHost, apiPort);
        if (got && alive.current) setCounts({ count: got.count ?? 0, action: got.action ?? 0 });
    }, [apiHost, apiPort]);

    useEffect(() => {
        alive.current = true;
        if (!enabled) return undefined;
        let timer = null;
        const stop = () => {
            if (timer !== null) clearInterval(timer);
            timer = null;
        };
        const start = () => {
            stop();
            refresh();
            timer = setInterval(refresh, POLL_MS);
        };
        const onVisibility = () => (document.visibilityState === 'visible' ? start() : stop());
        if (document.visibilityState === 'visible') start();
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
            alive.current = false;
            stop();
            document.removeEventListener('visibilitychange', onVisibility);
        };
    }, [enabled, refresh]);

    // Signed out: forget what the last person had.
    const [prevEnabled, setPrevEnabled] = useState(enabled);
    if (enabled !== prevEnabled) {
        setPrevEnabled(enabled);
        if (!enabled) setCounts({ count: 0, action: 0 });
    }

    return { count: counts.count, action: counts.action, refresh };
}
