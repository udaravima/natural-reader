import { useEffect, useRef, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';

/**
 * Compact "⋯ More" dropdown for the PDF options toolbar, shown only below the
 * `md` breakpoint (768 px). On phones the toolbar's secondary buttons (Fit,
 * Fit-Width, Ask page, and the converted-doc view/export controls) would
 * otherwise be pushed off the right edge; here they collapse into this menu.
 * At `md` and wider the dropdown is hidden and the inline buttons handle
 * everything, so the desktop toolbar is unchanged.
 *
 * Generic on purpose: it renders whatever `actions` it's handed and owns only
 * the open/close/outside-click/escape behaviour. Each entry is
 * `{ key, label, Icon, onClick, disabled?, active?, tone?: 'accent'|'danger',
 * sublabel? }`; falsy entries are skipped so callers can inline conditionals.
 * Selecting an item runs its `onClick` and closes the menu.
 */
export default function PdfToolbarMenu({ theme, actions = [] }) {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef(null);

    useEffect(() => {
        if (!open) return undefined;
        const onClick = (e) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
        };
        const onKey = (e) => {
            if (e.key === 'Escape') setOpen(false);
        };
        document.addEventListener('mousedown', onClick);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onClick);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const items = actions.filter(Boolean);
    if (items.length === 0) return null;

    const toneClass = (tone, active) => {
        if (tone === 'danger') return 'text-red-500';
        if (tone === 'accent' || active) return 'text-blue-500';
        return theme.textSecondary;
    };

    return (
        <div ref={wrapRef} className="relative md:hidden">
            <button
                onClick={() => setOpen((v) => !v)}
                className={`px-2 py-1.5 rounded-lg transition-all ${theme.hover} ${open ? 'text-blue-500' : theme.textSecondary}`}
                aria-haspopup="menu"
                aria-expanded={open}
                title="More actions"
            >
                <MoreHorizontal size={18} />
            </button>
            {open && (
                <div
                    role="menu"
                    className={`absolute right-0 mt-2 w-56 rounded-xl shadow-2xl border ${theme.border} ${theme.bgSecondary} overflow-hidden z-40`}
                >
                    {items.map(({ key, label, sublabel, Icon, onClick, disabled, active, tone }) => (
                        <button
                            key={key}
                            role="menuitem"
                            disabled={disabled}
                            onClick={() => { onClick?.(); setOpen(false); }}
                            className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${disabled ? 'opacity-50 cursor-not-allowed' : theme.hover} ${active ? theme.bgTertiary : ''} transition-colors`}
                        >
                            {Icon && <Icon size={16} className={`shrink-0 ${toneClass(tone, active)}`} />}
                            <span className="flex-1 min-w-0">
                                <span className={`block text-sm font-semibold truncate ${theme.text}`}>{label}</span>
                                {sublabel && (
                                    <span className={`block text-[10px] font-bold uppercase tracking-wider truncate ${theme.textMuted}`}>
                                        {sublabel}
                                    </span>
                                )}
                            </span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
