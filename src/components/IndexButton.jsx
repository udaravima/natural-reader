import { Database, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';

/**
 * Toolbar button that drives the "Index this document" flow.
 *
 * State comes from the parent (App.jsx) which owns the network calls — this
 * component just renders the right label/icon for the current state and fires
 * `onIndex` on click. PR 3 lands states up to `chunks_uploaded`; PR 4 will add
 * `indexing` (with progress) and `indexed`.
 */
export default function IndexButton({ theme, state, embeddedCount, chunkCount, onIndex }) {
    // states: 'idle' | 'registered' | 'chunks_uploaded' | 'indexing' | 'indexed' | 'failed' | 'uploading'
    if (state === 'uploading') {
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title="Uploading chunks to the server…"
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                Uploading
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
                title={`Indexed (${chunkCount ?? '?'} chunks). Click to re-index.`}
            >
                <CheckCircle2 size={14} className="inline mr-1" />
                Indexed
            </button>
        );
    }
    if (state === 'chunks_uploaded') {
        return (
            <button
                onClick={onIndex}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} text-amber-500 hover:text-amber-600 flex items-center`}
                title={`${chunkCount ?? '?'} chunks uploaded. Embeddings pending (PR 4).`}
            >
                <Database size={14} className="inline mr-1" />
                Re-upload
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
