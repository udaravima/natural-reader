import { MousePointer2, Loader2, Square, MessageSquare } from 'lucide-react';

export default function ReadSelectionButton({
    hasDocument,
    isReadingSelection,
    darkMode,
    onReadSelection,
    onStopSelectionRead,
    onAskAboutSelection,
    // The chat column is open beside the page (ChatSidePanel, 400 px; 440 px
    // from xl): sit left of it, not over its message box.
    besideChatPanel = false,
    // The Chat view: its composer fills the bottom of the screen, Send at the
    // bottom right, which is exactly where this button floats. Read Selection
    // is for text in the reader, so there it isn't shown (a reply has its own
    // read-aloud). A selection already being read keeps its Stop control, at
    // the top instead.
    inChat = false,
}) {
    if (!hasDocument) return null;
    // Beside the panel the button sits one panel width plus a 24 px gap from
    // the right edge, at any width: the panel is there at any width too (a
    // forced desktop layout can show it below 768 px).
    const right = besideChatPanel ? 'right-[424px] xl:right-[464px]' : 'right-4 md:right-6';

    // Reading indicator
    if (isReadingSelection) {
        const place = inChat ? `top-20 ${right}` : `bottom-24 md:bottom-6 ${right}`;
        return (
            <div className={`fixed ${place} z-[150] flex items-center gap-3`}>
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

    if (inChat) return null;

    // Read Selection (TTS) + optional Ask AI (chat) — paired floating actions.
    return (
        <div className={`fixed bottom-24 md:bottom-6 ${right} z-[150] flex items-center gap-2`}>
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
