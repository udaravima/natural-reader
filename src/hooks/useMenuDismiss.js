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
