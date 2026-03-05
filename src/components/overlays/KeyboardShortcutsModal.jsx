export default function KeyboardShortcutsModal({ show, theme, onClose }) {
    if (!show) return null;

    return (
        <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center"
            onClick={onClose}
        >
            <div
                className={`${theme.bgSecondary} rounded-2xl p-6 max-w-md w-full mx-4 shadow-2xl border ${theme.border}`}
                onClick={e => e.stopPropagation()}
            >
                <h3 className={`text-lg font-bold mb-4 ${theme.text}`}>⌨️ Keyboard Shortcuts</h3>
                <div className="space-y-3">
                    {[
                        ['Space', 'Play / Pause'],
                        ['Escape', 'Stop playback'],
                        ['Shift + ←', 'Previous sentence'],
                        ['Shift + →', 'Next sentence'],
                        ['Page Up', 'Previous page'],
                        ['Page Down', 'Next page'],
                        ['Ctrl + +', 'Zoom in'],
                        ['Ctrl + -', 'Zoom out'],
                        ['Ctrl + D', 'Toggle dark mode'],
                    ].map(([key, action]) => (
                        <div key={key} className={`flex justify-between items-center py-2 border-b ${theme.borderSecondary}`}>
                            <span className={theme.textSecondary}>{action}</span>
                            <kbd className={`px-3 py-1 ${theme.bgTertiary} rounded-lg text-sm font-mono font-bold ${theme.text}`}>
                                {key}
                            </kbd>
                        </div>
                    ))}
                </div>
                <button
                    onClick={onClose}
                    className="mt-6 w-full py-2 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 hover:shadow-lg transition-all"
                >
                    Got it!
                </button>
            </div>
        </div>
    );
}
