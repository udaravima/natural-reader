import { useEffect, useRef, useState } from 'react';
import { UserCircle, User, Settings as SettingsIcon, Moon, Sun, LogOut } from 'lucide-react';
import Avatar from './account/Avatar';

/**
 * Header profile dropdown: identity, a Settings shortcut, a dark-mode quick
 * toggle, and Log out. Persistent (lives in the always-mounted Header), so it's
 * reachable from every view — unlike the old logout that was buried in the
 * reader sidebar's account panel. Open/outside-click/Escape behaviour mirrors
 * PdfToolbarMenu / HeaderOverflowMenu.
 */
export default function ProfileMenu({ theme, user, apiHost, apiPort, darkMode, setDarkMode, onOpenSettings, onLogout }) {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef(null);

    useEffect(() => {
        if (!open) return undefined;
        const onClick = (e) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
        };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onClick);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onClick);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const identity = user?.name || user?.display_name || user?.email || 'Account';

    return (
        <div ref={wrapRef} className="relative">
            <button
                onClick={() => setOpen((v) => !v)}
                className={`p-2 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-blue-500 ${open ? 'text-blue-500' : ''}`}
                aria-haspopup="menu"
                aria-expanded={open}
                title="Account"
            >
                {user?.avatar_url
                    ? <Avatar apiHost={apiHost} apiPort={apiPort} url={user.avatar_url} name={identity} size={20} />
                    : <UserCircle size={20} />}
            </button>
            {open && (
                <div
                    role="menu"
                    className={`absolute right-0 mt-2 w-56 rounded-xl shadow-2xl border ${theme.border} ${theme.bgSecondary} overflow-hidden z-40`}
                >
                    <div className={`px-4 py-3 border-b ${theme.border} flex items-center gap-3`}>
                        <Avatar apiHost={apiHost} apiPort={apiPort} url={user?.avatar_url} name={identity} size={32} />
                        <div className="min-w-0">
                            <p className={`text-sm font-bold truncate ${theme.text}`}>{identity}</p>
                            {user?.email && user.email !== identity && (
                                <p className={`text-[11px] truncate ${theme.textMuted}`}>{user.email}</p>
                            )}
                        </div>
                    </div>
                    <button
                        role="menuitem"
                        onClick={() => { onOpenSettings?.('profile'); setOpen(false); }}
                        className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${theme.hover} transition-colors`}
                    >
                        <User size={16} className={`shrink-0 ${theme.textSecondary}`} />
                        <span className={`text-sm font-semibold ${theme.text}`}>Profile</span>
                    </button>
                    <button
                        role="menuitem"
                        onClick={() => { onOpenSettings?.(); setOpen(false); }}
                        className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${theme.hover} transition-colors`}
                    >
                        <SettingsIcon size={16} className={`shrink-0 ${theme.textSecondary}`} />
                        <span className={`text-sm font-semibold ${theme.text}`}>Settings</span>
                    </button>
                    <button
                        role="menuitem"
                        onClick={() => setDarkMode((v) => !v)}
                        className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${theme.hover} transition-colors`}
                    >
                        {darkMode
                            ? <Sun size={16} className={`shrink-0 ${theme.textSecondary}`} />
                            : <Moon size={16} className={`shrink-0 ${theme.textSecondary}`} />}
                        <span className={`text-sm font-semibold ${theme.text}`}>{darkMode ? 'Light mode' : 'Dark mode'}</span>
                    </button>
                    <button
                        role="menuitem"
                        onClick={() => { setOpen(false); onLogout?.(); }}
                        className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${theme.hover} transition-colors border-t ${theme.border}`}
                    >
                        <LogOut size={16} className="shrink-0 text-red-500" />
                        <span className={`text-sm font-semibold ${theme.text}`}>Log out</span>
                    </button>
                </div>
            )}
        </div>
    );
}
