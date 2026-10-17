import { Play, Pause, Square, BookOpen } from 'lucide-react';

/**
 * The narration's controls outside the reader (chat, library, admin): the
 * narration keeps playing there, so it needs a pause and a stop, and a way
 * back to the page being read. Shown while the narration has a position in
 * the document: Pause keeps it (and this bar), Stop clears it (and hides it).
 */
export default function NarrationMiniPlayer({
    theme, docName, currentPage, numPages, isPlaying, pausedForChat,
    onPlayPause, onStop, onShowReader,
}) {
    const where = numPages > 1 ? ` · p. ${currentPage}/${numPages}` : '';
    return (
        <div
            role="group"
            aria-label="Narration"
            className={`flex items-center gap-1 ${theme.bgTertiary} p-1 rounded-xl border ${theme.border} min-w-0`}
        >
            <button
                onClick={onPlayPause}
                className={`p-1.5 rounded-lg transition-colors ${isPlaying ? 'text-amber-500' : 'text-blue-500'} ${theme.hover}`}
                title={isPlaying ? 'Pause the narration' : 'Resume the narration'}
                aria-label={isPlaying ? 'Pause the narration' : 'Resume the narration'}
            >
                {isPlaying ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
            </button>
            <button
                onClick={onStop}
                className={`p-1.5 rounded-lg ${theme.hover} ${theme.textMuted} hover:text-red-500`}
                title="Stop the narration"
                aria-label="Stop the narration"
            >
                <Square size={12} fill="currentColor" />
            </button>
            <button
                onClick={onShowReader}
                className={`hidden sm:flex items-center gap-1.5 px-2 min-w-0 text-[10px] font-bold ${theme.textSecondary} hover:text-blue-500`}
                title="Back to the page being read"
            >
                <BookOpen size={12} className="shrink-0" />
                <span className="truncate max-w-[180px]">
                    {pausedForChat ? 'Paused for the reply' : (docName || 'Reading')}{where}
                </span>
            </button>
        </div>
    );
}
