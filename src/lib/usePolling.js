import { useEffect, useRef } from 'react';

/**
 * Call `fn` every `intervalMs` while `active`, but only while the browser tab is visible; when the tab comes back it
 * reads once at once. It never starts a read while the last one is still out, and stops when `active` turns false or
 * the component unmounts. `fn` may change on every render: the latest one is used, and the clock isn't restarted.
 */
export function usePolling(fn, active, intervalMs = 2000) {
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });

  useEffect(() => {
    if (!active) return undefined;
    let out = false;
    const run = async () => {
      if (out || document.visibilityState !== 'visible') return;
      out = true;
      try {
        await latest.current();
      } catch {
        // the caller shows its own errors; the next tick tries again
      } finally {
        out = false;
      }
    };
    const timer = setInterval(run, intervalMs);
    document.addEventListener('visibilitychange', run);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', run);
    };
  }, [active, intervalMs]);
}
