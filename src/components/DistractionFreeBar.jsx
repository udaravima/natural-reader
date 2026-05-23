import { Play, Pause, SkipBack, SkipForward, X } from 'lucide-react';

/**
 * Floating control bar shown while in distraction-free mode.
 *
 * Replaces the bare "Exit" pill with something useful for the reader: prev/
 * play/next sentence controls, the current page indicator, and the Exit
 * action — all packed into one compact pill at the bottom-center. In chat
 * mode (or when no document is open), playback controls collapse and only
 * the page-less Exit button remains.
 *
 * Designed to stay out of the way: bottom-center with `pointer-events-auto`
 * only on the bar itself, semi-transparent backdrop, fixed z-index above the
 * page content but below toasts.
 */
export default function DistractionFreeBar({
    onExit,
    hasDocument,
    inChat,
    isPlaying,
    handlePlayPause,
    skipToNextSentence,
    skipToPrevSentence,
    currentPage,
    numPages,
    setCurrentPage,
}) {
    const showPlayback = !inChat && hasDocument;
    return (
        <div className="fixed inset-x-0 bottom-3 z-50 flex justify-center pointer-events-none">
            <div className="pointer-events-auto flex items-center gap-1 px-2 py-1.5 rounded-full bg-slate-900/80 text-white shadow-2xl backdrop-blur-md border border-white/10">
                {showPlayback && (
                    <>
                        <button
                            onClick={skipToPrevSentence}
                            className="p-2 rounded-full hover:bg-white/10 transition-colors"
                            title="Previous sentence (Shift + ←)"
                        >
                            <SkipBack size={16} />
                        </button>
                        <button
                            onClick={handlePlayPause}
                            className={`p-2.5 rounded-full transition-colors ${isPlaying ? 'bg-amber-500 hover:bg-amber-600' : 'bg-blue-600 hover:bg-blue-700'}`}
                            title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
                        >
                            {isPlaying ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
                        </button>
                        <button
                            onClick={skipToNextSentence}
                            className="p-2 rounded-full hover:bg-white/10 transition-colors"
                            title="Next sentence (Shift + →)"
                        >
                            <SkipForward size={16} />
                        </button>

                        <div className="w-px h-5 bg-white/15 mx-1" />

                        {/* Page indicator — clickable number input lets you
                            jump pages without leaving distraction-free mode. */}
                        <div className="flex items-center gap-1 px-2 text-xs font-bold">
                            <input
                                type="number"
                                min="1"
                                max={numPages || 1}
                                value={currentPage}
                                onChange={(e) => {
                                    const v = parseInt(e.target.value, 10);
                                    if (Number.isFinite(v) && v >= 1 && v <= numPages) setCurrentPage(v);
                                }}
                                className="w-10 text-center bg-transparent outline-none"
                            />
                            <span className="text-white/60">/ {numPages || '?'}</span>
                        </div>

                        <div className="w-px h-5 bg-white/15 mx-1" />
                    </>
                )}

                <button
                    onClick={onExit}
                    className="px-2.5 py-1.5 rounded-full hover:bg-white/10 transition-colors flex items-center gap-1.5 text-xs font-bold"
                    title="Exit distraction-free mode (F)"
                >
                    <X size={14} />
                    <span>Exit</span>
                </button>
            </div>
        </div>
    );
}
