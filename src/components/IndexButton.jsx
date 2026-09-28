import { Database, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';

/**
 * Toolbar button that drives the "Index this document" flow.
 *
 * State comes from the parent (App.jsx) which owns the network calls — this
 * component just renders the right label/icon for the current state and fires
 * `onIndex` on click.
 */
export default function IndexButton({ theme, state, embeddedCount, chunkCount, onIndex }) {
    // states: 'idle' | 'uploading' | 'stored' | 'extracting' | 'extracted' | 'indexing' | 'indexed' | 'failed'
    if (state === 'uploading') {
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title="Uploading the file…"
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                Uploading
            </button>
        );
    }
    if (state === 'extracting') {
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title="The server is reading the document's text"
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                Extracting
            </button>
        );
    }
    if (state === 'indexing') {
        const progress = (chunkCount && embeddedCount != null)
            ? ` ${embeddedCount}/${chunkCount}`
            : '';
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title="Embedding chunks — this can take a minute"
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                Indexing{progress}
            </button>
        );
    }
    if (state === 'indexed') {
        return (
            <button
                onClick={onIndex}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} text-emerald-500 hover:text-emerald-600 flex items-center`}
                title={`Indexed (${chunkCount ?? '?'} chunks). Click to re-index — only if nobody else uses it.`}
            >
                <CheckCircle2 size={14} className="inline mr-1" />
                Indexed
            </button>
        );
    }
    // 'stored' (interrupted before extraction started) and 'extracted'
    // (chunks exist, embeddings don't) both need a click to move forward —
    // nothing runs on its own, so both get the clickable "Resume" button.
    if (state === 'stored' || state === 'extracted') {
        return (
            <button
                onClick={onIndex}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} text-amber-500 hover:text-amber-600 flex items-center`}
                title="Indexing was interrupted — click to resume"
            >
                <Database size={14} className="inline mr-1" />
                Resume
            </button>
        );
    }
    if (state === 'failed') {
        return (
            <button
                onClick={onIndex}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} text-red-500 hover:text-red-600 flex items-center`}
                title="Indexing failed — click to retry"
            >
                <AlertTriangle size={14} className="inline mr-1" />
                Retry index
            </button>
        );
    }
    // 'idle' or anything unknown
    return (
        <button
            onClick={onIndex}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} ${theme.textSecondary} hover:text-blue-500 flex items-center`}
            title="Send this document's text to the server for chat-aware retrieval"
        >
            <Database size={14} className="inline mr-1" />
            Index
        </button>
    );
}
