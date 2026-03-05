import { MousePointer2, Loader2, Square } from 'lucide-react';

export default function ReadSelectionButton({
    pdfDoc,
    isReadingSelection,
    darkMode,
    onReadSelection,
    onStopSelectionRead,
}) {
    if (!pdfDoc) return null;

    // Reading indicator
    if (isReadingSelection) {
        return (
            <div className="fixed bottom-6 right-6 z-[150] flex items-center gap-3">
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

    // Read selection button
    return (
        <button
            onClick={onReadSelection}
            className={`fixed bottom-6 right-6 z-[150] px-4 py-3 rounded-2xl shadow-xl flex items-center gap-2 transition-all hover:scale-105 ${darkMode ? 'bg-gradient-to-r from-blue-600 to-cyan-600 text-white' : 'bg-gradient-to-r from-blue-500 to-cyan-500 text-white'}`}
            title="Select text and click to read it aloud"
        >
            <MousePointer2 size={18} />
            <span className="text-sm font-bold">Read Selection</span>
        </button>
    );
}
