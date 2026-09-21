import { BookOpen, MessageSquare, Library, Shield } from 'lucide-react';

/**
 * Reader / Chat / Library / Admin view toggle. The SPA has no router —
 * viewMode state IS the routing. The admin shield is rendered only for
 * admins; a member never sees it, and the server-side rails + boot coercion
 * (useViewModeGuard) make that hiding cosmetic rather than the security
 * boundary. Library, unlike admin, is available to any authenticated
 * active user — no guard entry needed.
 */
export default function ViewSwitcher({ theme, viewMode, setViewMode, isAdmin }) {
    const inChat = viewMode === 'chat';
    const inLibrary = viewMode === 'library';
    const inAdmin = viewMode === 'admin';
    const btn = (active) =>
        `px-2.5 py-1.5 rounded-lg text-[10px] font-bold flex items-center gap-1.5 transition-colors ${
            active ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} hover:text-blue-500`
        }`;
    return (
        <div className={`flex p-1 rounded-xl border ${theme.border} ${theme.bgTertiary}`}>
            <button
                onClick={() => setViewMode('reader')}
                className={btn(!inChat && !inLibrary && !inAdmin)}
                title="Reader mode"
            >
                <BookOpen size={12} />
                <span className="hidden sm:inline">Reader</span>
            </button>
            <button
                onClick={() => setViewMode('chat')}
                className={btn(inChat)}
                title="Chat mode"
            >
                <MessageSquare size={12} />
                <span className="hidden sm:inline">Chat</span>
            </button>
            <button
                onClick={() => setViewMode('library')}
                className={btn(inLibrary)}
                title="Library"
            >
                <Library size={12} />
                <span className="hidden sm:inline">Library</span>
            </button>
            {isAdmin && (
                <button
                    onClick={() => setViewMode('admin')}
                    className={btn(inAdmin)}
                    title="Admin console"
                >
                    <Shield size={12} />
                    <span className="hidden sm:inline">Admin</span>
                </button>
            )}
        </div>
    );
}
