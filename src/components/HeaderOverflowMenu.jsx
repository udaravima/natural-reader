import { useEffect, useRef, useState } from 'react';
import {
    MoreHorizontal, Zap, Moon, Sun, Download, Keyboard, Maximize2,
    Home, Library, Loader2,
} from 'lucide-react';

/**
 * Overflow menu for the Header — visible at any viewport narrower than `lg`
 * (1024 px), i.e. phones AND tablets / squeezed-down desktop windows.
 *
 * The Header has ~10 buttons on full desktop. The secondary ones are gated
 * with `hidden lg:*` so they don't crowd a narrow layout — without this
 * menu they'd just vanish, leaving the user with no way to toggle dark
 * mode, change TTS backend, download audio, see shortcuts, etc. This
 * component (`lg:hidden`) surfaces all of those actions in a small
 * dropdown. At `lg` and wider the dropdown is hidden because the inline
 * buttons handle everything.
 *
 * Each `actions` entry is `{ key, label, icon: Icon, onClick, disabled?,
 * tone?: 'default'|'accent', sublabel?, show?: boolean }`. Entries with
 * `show: false` are silently skipped — convenient for conditional items like
 * "Download page audio" that only appear when Kokoro is selected and a
 * document is open.
 */
export default function HeaderOverflowMenu({
    theme,
    darkMode,
    isLocalhost, setIsLocalhost,
    setDarkMode,
    onDownloadPageAudio, isDownloading, downloadEnabled,
    onShowShortcuts,
    onEnterDistractionFree,
    onGoHome,
    onDownloadBookAudio, bookProgress,
    hasDocument,
}) {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef(null);

    // Click outside / Escape closes.
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

    const close = () => setOpen(false);

    const actions = [
        hasDocument && onGoHome && {
            key: 'home',
            label: 'Back to library',
            Icon: Home,
            onClick: () => { close(); onGoHome(); },
        },
        hasDocument && onEnterDistractionFree && {
            key: 'distraction',
            label: 'Distraction-free mode',
            sublabel: 'F',
            Icon: Maximize2,
            onClick: () => { close(); onEnterDistractionFree(); },
        },
        setIsLocalhost && {
            key: 'tts-backend',
            label: isLocalhost ? 'Voice backend: Kokoro' : 'Voice backend: System',
            sublabel: 'Tap to switch',
            Icon: Zap,
            tone: isLocalhost ? 'accent' : 'default',
            onClick: () => setIsLocalhost(v => !v),
        },
        setDarkMode && {
            key: 'dark',
            label: darkMode ? 'Light mode' : 'Dark mode',
            sublabel: 'Ctrl + D',
            Icon: darkMode ? Sun : Moon,
            onClick: () => { close(); setDarkMode(v => !v); },
        },
        onDownloadPageAudio && downloadEnabled && {
            key: 'download-page',
            label: 'Download page audio',
            Icon: isDownloading ? Loader2 : Download,
            disabled: isDownloading,
            onClick: () => { close(); onDownloadPageAudio(); },
        },
        onDownloadBookAudio && hasDocument && {
            key: 'download-book',
            label: bookProgress
                ? `Audiobook ${bookProgress.current}/${bookProgress.total}`
                : 'Download audiobook',
            sublabel: bookProgress?.label,
            Icon: bookProgress ? Loader2 : Library,
            disabled: !!bookProgress,
            onClick: () => { close(); onDownloadBookAudio(); },
        },
        onShowShortcuts && {
            key: 'shortcuts',
            label: 'Keyboard shortcuts',
            Icon: Keyboard,
            onClick: () => { close(); onShowShortcuts(); },
        },
    ].filter(Boolean);

    if (actions.length === 0) return null;

    return (
        <div ref={wrapRef} className="relative lg:hidden">
            <button
                onClick={() => setOpen(v => !v)}
                className={`p-2.5 ${theme.bgTertiary} rounded-xl ${theme.hover} transition-all ${theme.textSecondary} hover:text-blue-500 ${open ? 'text-blue-500' : ''}`}
                aria-haspopup="menu"
                aria-expanded={open}
                title="More actions"
            >
                <MoreHorizontal size={20} />
            </button>
            {open && (
                <div
                    role="menu"
                    className={`absolute right-0 mt-2 w-64 rounded-xl shadow-2xl border ${theme.border} ${theme.bgSecondary} overflow-hidden z-40`}
                >
                    {actions.map(({ key, label, sublabel, Icon, onClick, disabled, tone }) => (
                        <button
                            key={key}
                            role="menuitem"
                            onClick={onClick}
                            disabled={disabled}
                            className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${disabled ? 'opacity-50 cursor-not-allowed' : theme.hover} transition-colors`}
                        >
                            <Icon
                                size={16}
                                className={`shrink-0 ${
                                    tone === 'accent' ? 'text-green-500' : theme.textSecondary
                                } ${Icon === Loader2 ? 'animate-spin' : ''}`}
                            />
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
