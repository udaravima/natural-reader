import { MousePointer2, Loader2, Square, MessageSquare } from 'lucide-react';

export default function ReadSelectionButton({
    hasDocument,
    isReadingSelection,
    darkMode,
    onReadSelection,
    onStopSelectionRead,
    onAskAboutSelection,
}) {
    if (!hasDocument) return null;

    // Reading indicator
    if (isReadingSelection) {
        return (
            <div className="fixed bottom-24 right-4 md:bottom-6 md:right-6 z-[150] flex items-center gap-3">
                <div className={`px-4 py-3 rounded-2xl shadow-xl flex items-center gap-2 ${darkMode ? 'bg-green-700' : 'bg-green-500'} text-white`}>
                    <Loader2 size={18} className="animate-spin" />
                    <span className="text-sm font-bold">Reading...</span>
                </div>
                <button
                    onClick={onStopSelectionRead}
                    className={`p-3 rounded-full shadow-xl ${darkMode ? 'bg-red-700' : 'bg-red-500'} text-white hover:scale-105 transition-all`}
                    title="Stop reading"
                >
                    <Square size={16} />
                </button>
            </div>
        );
    }

    // Read Selection (TTS) + optional Ask AI (chat) — paired floating actions.
    return (
        <div className="fixed bottom-24 right-4 md:bottom-6 md:right-6 z-[150] flex items-center gap-2">
            <button
                onClick={onReadSelection}
                className={`px-4 py-3 rounded-2xl shadow-xl flex items-center gap-2 transition-all hover:scale-105 ${darkMode ? 'bg-gradient-to-r from-blue-600 to-cyan-600 text-white' : 'bg-gradient-to-r from-blue-500 to-cyan-500 text-white'}`}
                title="Select text and click to read it aloud"
            >
                <MousePointer2 size={18} />
                <span className="text-sm font-bold">Read Selection</span>
            </button>
            {onAskAboutSelection && (
                <button
                    onClick={onAskAboutSelection}
                    className={`px-4 py-3 rounded-2xl shadow-xl flex items-center gap-2 transition-all hover:scale-105 ${darkMode ? 'bg-gradient-to-r from-purple-600 to-fuchsia-600 text-white' : 'bg-gradient-to-r from-purple-500 to-fuchsia-500 text-white'}`}
                    title="Select text and click to ask the model about it"
                >
                    <MessageSquare size={18} />
                    <span className="text-sm font-bold">Ask AI</span>
                </button>
            )}
        </div>
    );
}
