import { useEffect } from 'react';

// Priority order used to pick a fallback view when the persisted one isn't
// permitted: prefer reader, then chat, then admin.
const VIEW_ORDER = ['reader', 'chat', 'admin'];

// Views any active user may occupy without a matching capability. The
// reader/chat/admin views are gated by a same-named capability (view name ===
// capability name), but Library is intentionally ungated: available to every
// authenticated active user. It has no capability to match, so without
// listing it here the guard would treat it as an unknown view and bounce to
// the first held capability — the "click it, land on reader" bug. (Settings
// was a view too; it is a dialog now, so a browser still holding 'settings'
// lands on the first permitted view.)
const CAP_FREE_VIEWS = ['library'];

/**
 * Boot coercion for the capability-gated views (reader/chat/admin): a persisted
 * viewMode is only valid while the current user actually holds the matching
 * capability. Capability-free views (CAP_FREE_VIEWS, e.g. Library) are always
 * permitted for an active user and never coerced. Waits for the auth probe to
 * resolve ('active') so a freshly-loading session isn't bounced before
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
    // Permitted if it's a capability-free view, or a gated one whose capability the user holds. Not every
    // capability is a view: `talk` and `docling` name none.
    if (CAP_FREE_VIEWS.includes(viewMode) || (VIEW_ORDER.includes(viewMode) && capsList.includes(viewMode))) return;
    const fallback = VIEW_ORDER.find((v) => capsList.includes(v));
    if (fallback) setViewMode(fallback);
  }, [viewMode, setViewMode, authState, caps]);
}
