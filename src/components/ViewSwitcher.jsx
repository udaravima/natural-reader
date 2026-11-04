import { useRef, useState } from 'react';
import { BookOpen, ChevronDown, MessageSquare, Library, Shield } from 'lucide-react';
import { useMenuDismiss } from '../hooks/useMenuDismiss';

/**
 * Reader / Chat / Library / Admin view toggle. The SPA has no router — viewMode
 * state IS the routing. Reader and Chat are each rendered only when the caller
 * says the user holds the matching capability (canReader/canChat, default true
 * so existing callers keep always-visible behavior); the Admin shield only for
 * admins. Library, unlike those, is available to any authenticated active user —
 * no capability gate or guard entry needed. The server-side rails + boot
 * coercion (useViewModeGuard) make this hiding cosmetic, not the security
 * boundary.
 *
 * Two shapes of the same choice: ViewSwitcher, a row of buttons, and ViewMenu, one button with a menu, for a phone,
 * where the row doesn't fit (Header shows one or the other by width).
 */

/** The views this person may open, in order. */
function viewsFor({ isAdmin, canReader = true, canChat = true }) {
    return [
        canReader && { id: 'reader', label: 'Reader', title: 'Reader mode', Icon: BookOpen },
        canChat && { id: 'chat', label: 'Chat', title: 'Chat mode', Icon: MessageSquare },
        { id: 'library', label: 'Library', title: 'Library', Icon: Library },
        isAdmin && { id: 'admin', label: 'Admin', title: 'Admin console', Icon: Shield },
    ].filter(Boolean);
}

/** Which view is lit: anything that isn't chat, the library or the console is the reader. */
const openView = (viewMode) => (['chat', 'library', 'admin'].includes(viewMode) ? viewMode : 'reader');

export default function ViewSwitcher({ theme, viewMode, setViewMode, isAdmin, canReader = true, canChat = true, className = 'flex' }) {
    const open = openView(viewMode);
    const btn = (active) =>
        `px-2.5 py-1.5 rounded-lg text-[10px] font-bold flex items-center gap-1.5 transition-colors ${
            active ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} hover:text-blue-500`
        }`;
    return (
        <div className={`${className} p-1 rounded-xl border ${theme.border} ${theme.bgTertiary}`}>
            {viewsFor({ isAdmin, canReader, canChat }).map(({ id, label, title, Icon }) => (
                <button key={id} onClick={() => setViewMode(id)} className={btn(open === id)} title={title}>
                    <Icon size={12} />
                    <span className="hidden sm:inline">{label}</span>
                </button>
            ))}
        </div>
    );
}

/**
 * The view switch as one button, showing the open view's icon, with the views in a menu. Escape or a press outside
 * closes it (useMenuDismiss); `data-own-keys` keeps Space and Escape from the reader's narration shortcuts.
 */
export function ViewMenu({ theme, viewMode, setViewMode, isAdmin, canReader = true, canChat = true, className = '' }) {
    const [menuOpen, setMenuOpen] = useState(false);
    const wrapRef = useRef(null);
    const openerRef = useRef(null);
    useMenuDismiss({ open: menuOpen, onClose: () => setMenuOpen(false), wrapRef, openerRef });
    const views = viewsFor({ isAdmin, canReader, canChat });
    const current = views.find((v) => v.id === openView(viewMode)) ?? views[0];
    const CurrentIcon = current.Icon;
    return (
        <div ref={wrapRef} data-own-keys className={`relative ${className}`}>
            <button
                ref={openerRef}
                onClick={() => setMenuOpen((v) => !v)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label={`View: ${current.label}`}
                className={`flex items-center justify-center gap-0.5 p-2.5 rounded-xl ${theme.bgTertiary} ${theme.hover} transition-all text-blue-500`}
            >
                <CurrentIcon size={20} aria-hidden="true" />
                <ChevronDown size={12} aria-hidden="true" className={theme.textSecondary} />
            </button>
            {menuOpen && (
                // Opens to the right of the button: on a phone the button sits about 160 px from the left edge, and a
                // menu 192 px wide anchored on its right would run off the screen.
                <div role="menu" aria-label="Views"
                    className={`absolute left-0 mt-2 w-48 rounded-xl shadow-2xl border ${theme.border} ${theme.bgSecondary} overflow-hidden z-40`}>
                    {views.map(({ id, label, Icon }) => (
                        <button
                            key={id}
                            role="menuitemradio"
                            aria-checked={id === current.id}
                            onClick={() => { setMenuOpen(false); setViewMode(id); }}
                            className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm font-semibold ${theme.hover} transition-colors ${id === current.id ? 'text-blue-500' : theme.text}`}
                        >
                            <Icon size={16} aria-hidden="true" className="shrink-0" />
                            {label}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
