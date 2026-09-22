import { useEffect } from 'react';

// Priority order used to pick a fallback view when the persisted one isn't
// permitted: prefer reader, then chat, then admin.
const VIEW_ORDER = ['reader', 'chat', 'admin'];

/**
 * Boot coercion for the reader/chat/admin views: a persisted viewMode is
 * only valid while the current user actually holds the matching capability
 * (view name === capability name). Waits for the auth probe to resolve
 * ('active') so a freshly-loading session isn't bounced before
 * /v1/auth/me has answered. Everything else (pending, disabled, anonymous,
 * error) renders the AuthGate, so 'active' is the only state where a
 * decision is meaningful.
 *
 * When caps is empty, there is no permitted fallback — AuthGate's
 * NoAccessScreen already covers that case, so this hook simply does nothing
 * rather than looping trying to set an invalid view.
 */
export function useViewModeGuard({ viewMode, setViewMode, authState, caps }) {
  useEffect(() => {
    if (authState !== 'active') return;
    const capsList = caps ?? [];
    if (capsList.includes(viewMode)) return; // current view is still permitted
    const fallback = VIEW_ORDER.find((v) => capsList.includes(v));
    if (fallback) setViewMode(fallback);
  }, [viewMode, setViewMode, authState, caps]);
}
