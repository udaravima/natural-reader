import React from 'react';
import {
    Play, Pause, ChevronLeft, ChevronRight,
    SkipForward, SkipBack
} from 'lucide-react';

export default function MobileBottomNav({
    theme,
    effectiveIsMobile,
    pdfDoc,
    currentPage, setCurrentPage,
    numPages,
    currentSentenceIndex, setCurrentSentenceIndex,
    textItems,
    isPlaying,
    handlePlayPause,
    skipToNextSentence,
}) {
    if (!effectiveIsMobile || !pdfDoc) return null;

    return (
        <nav className={`fixed bottom-0 left-0 right-0 z-50 ${theme.bgSecondary} border-t ${theme.border} px-2 py-2 safe-area-pb`}>
            <div className="flex items-center justify-around gap-1">
                {/* Previous Page */}
                <button
                    onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                    className={`p-3 rounded-xl transition-all ${currentPage <= 1 ? 'opacity-30' : theme.hover + ' ' + theme.textSecondary}`}
                >
                    <ChevronLeft size={24} />
                </button>

                {/* Previous Sentence */}
                <button
                    onClick={() => setCurrentSentenceIndex(prev => Math.max(-1, prev - 1))}
                    className={`p-3 rounded-xl ${theme.hover} ${theme.textSecondary}`}
                >
                    <SkipBack size={22} />
                </button>

                {/* Play/Pause */}
                <button
                    onClick={handlePlayPause}
                    className={`p-4 rounded-2xl flex items-center justify-center transition-all ${isPlaying
                        ? 'bg-amber-500 text-white shadow-lg shadow-amber-500/30'
                        : 'bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-lg shadow-blue-500/30'
                        }`}
                >
                    {isPlaying ? <Pause size={28} /> : <Play size={28} className="ml-1" />}
                </button>

                {/* Next Sentence */}
                <button
                    onClick={skipToNextSentence}
                    className={`p-3 rounded-xl ${theme.hover} ${theme.textSecondary}`}
                >
                    <SkipForward size={22} />
                </button>

                {/* Next Page */}
                <button
                    onClick={() => setCurrentPage(p => Math.min(numPages, p + 1))}
                    disabled={currentPage >= numPages}
                    className={`p-3 rounded-xl transition-all ${currentPage >= numPages ? 'opacity-30' : theme.hover + ' ' + theme.textSecondary}`}
                >
                    <ChevronRight size={24} />
                </button>
            </div>

            {/* Progress Indicator */}
            <div className={`mt-2 mx-4 text-center text-[10px] font-bold ${theme.textMuted}`}>
                Page {currentPage}/{numPages} • Sentence {currentSentenceIndex + 1}/{textItems.length}
            </div>
        </nav>
    );
}
