import { useEffect, useRef } from 'react';

/**
 * Look up the saved workspace folder once the reader is ready and a user is
 * signed in, and again whenever that user changes. The saved workspace
 * belongs to the signed-in user (src/db.js), so looking before /v1/auth/me
 * has named them would find nothing and never try again.
 *
 * `onSaved(saved, userId)` gets the lookup's result (null when that user has
 * none); a lookup superseded by a newer one is dropped. It runs in the
 * promise's callback, so it may set state.
 */
export function useWorkspaceRestore({ ready, userId, getSaved, onSaved }) {
    // The latest callback, read when a lookup lands (App passes a new one
    // each render); refreshed after commit, never during render.
    const onSavedRef = useRef(onSaved);
    useEffect(() => { onSavedRef.current = onSaved; }, [onSaved]);

    useEffect(() => {
        if (!ready || !userId) return undefined;
        let current = true;
        getSaved()
            .then((saved) => { if (current) return onSavedRef.current(saved, userId); return undefined; })
            .catch((e) => console.warn('Workspace restore failed:', e)); // best-effort, never crashes mount
        return () => { current = false; };
    }, [ready, userId, getSaved]);
}
