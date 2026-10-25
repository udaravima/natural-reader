import { useEffect, useRef } from 'react';

/**
 * Closes an open dropdown menu on a mouse press outside it or on Escape, and
 * after Escape gives the focus back to the button that opened it.
 *
 * `wrapRef` holds the opener and the menu together, so a press on either is
 * "inside". `openerRef` is the opener button. The focus only goes back when it
 * is still in the wrapper or nowhere (on the page): an Escape pressed from
 * another field, with the menu left open behind it, closes the menu but does
 * not pull the person out of what they were typing.
 *
 * An Escape that closes the menu is the menu's: it is stopped at `document`,
 * so it never reaches the reader's keyboard shortcuts on `window` (where Escape
 * stops the narration). Those shortcuts skip a key whose target is inside a
 * `data-own-keys` wrapper, but a menu opened by a click that took no focus
 * (Safari), or left open while the focus moved on, has `<body>` as the target.
 * The event path runs target, then `document`, then `window`, so this listener
 * is always ahead of theirs. With the menu closed nothing is listening here, and
 * Escape goes on to the shortcuts as usual.
 *
 * Listens on `document` only while `open`, so a closed menu costs nothing.
 */
export function useMenuDismiss({ open, onClose, wrapRef, openerRef }) {
    const closeRef = useRef(onClose);
    useEffect(() => { closeRef.current = onClose; });

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target)) closeRef.current();
        };
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            e.stopPropagation();
            closeRef.current();
            const at = document.activeElement;
            if (!at || at === document.body || wrapRef.current?.contains(at)) openerRef.current?.focus();
        };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open, wrapRef, openerRef]);
}
