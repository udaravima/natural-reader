import { BookOpen, MessageSquare, Shield } from 'lucide-react';

/**
 * Reader / Chat / Admin view toggle. The SPA has no router — viewMode state
 * IS the routing. Each entry is rendered only when the caller says the user
 * holds the matching capability; a reader-only user never sees the Chat
 * switch, a non-admin never sees the Admin shield. The server-side rails +
 * boot coercion (useViewModeGuard) make that hiding cosmetic rather than the
 * security boundary. canReader/canChat default to true so existing callers
 * that don't pass them keep today's always-visible behavior.
 */
export default function ViewSwitcher({ theme, viewMode, setViewMode, isAdmin, canReader = true, canChat = true }) {
    const inChat = viewMode === 'chat';
    const inAdmin = viewMode === 'admin';
    const btn = (active) =>
        `px-2.5 py-1.5 rounded-lg text-[10px] font-bold flex items-center gap-1.5 transition-colors ${
            active ? 'bg-blue-600 text-white shadow' : `${theme.textSecondary} hover:text-blue-500`
        }`;
    return (
        <div className={`flex p-1 rounded-xl border ${theme.border} ${theme.bgTertiary}`}>
            {canReader && (
                <button
                    onClick={() => setViewMode('reader')}
                    className={btn(!inChat && !inAdmin)}
                    title="Reader mode"
                >
                    <BookOpen size={12} />
                    <span className="hidden sm:inline">Reader</span>
                </button>
            )}
            {canChat && (
                <button
                    onClick={() => setViewMode('chat')}
                    className={btn(inChat)}
                    title="Chat mode"
                >
                    <MessageSquare size={12} />
                    <span className="hidden sm:inline">Chat</span>
                </button>
            )}
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
