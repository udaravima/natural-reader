import React from 'react';
import { PlayCircle, MousePointer2 } from 'lucide-react';

export default function ContextMenu({ contextMenu, theme, textItems, onContinueFromHere, onCopySentence, onClose }) {
    if (!contextMenu) return null;

    return (
        <div
            className={`fixed z-[300] ${theme.bgSecondary} rounded-xl shadow-2xl border ${theme.border} py-2 min-w-[180px]`}
            style={{ left: contextMenu.x, top: contextMenu.y }}
        >
            <button
                onClick={() => onContinueFromHere(contextMenu.sentenceIndex)}
                className={`w-full px-4 py-2.5 text-left text-sm flex items-center gap-3 ${theme.hover} ${theme.text} hover:text-blue-500`}
            >
                <PlayCircle size={16} className="text-blue-500" />
                Continue from here
            </button>
            <button
                onClick={() => {
                    const text = textItems[contextMenu.sentenceIndex];
                    if (text) {
                        window.getSelection().removeAllRanges();
                        navigator.clipboard.writeText(text);
                        onCopySentence();
                    }
                    onClose();
                }}
                className={`w-full px-4 py-2.5 text-left text-sm flex items-center gap-3 ${theme.hover} ${theme.text} hover:text-blue-500`}
            >
                <MousePointer2 size={16} className="text-slate-500" />
                Copy sentence
            </button>
        </div>
    );
}
