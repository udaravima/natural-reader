import { FileText, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';

/**
 * Toolbar button that drives the docling Convert-to-Markdown flow.
 *
 * State is owned by the parent (App.jsx) — this component just renders the
 * appropriate label/icon and fires `onClick` when interactive. Mirrors the
 * shape of `IndexButton` so the toolbar stays visually consistent.
 *
 * states: 'idle' | 'uploading' | 'converting' | 'converted' | 'failed'
 * (or the server's 'conversion_failed', the same to the reader).
 *
 * `progress` is the server's `conversion_progress` while converting (null
 * before Docling's first report): { phase, done?, total?, label? }.
 */
function converting(progress) {
    const { phase, done, total, label } = progress || {};
    const counted = Number.isFinite(done) && Number.isFinite(total) && total > 0;
    if (phase === 'initialize') {
        return { text: 'Preparing', title: 'Docling is getting ready — loading its models is slow the first time.' };
    }
    if (phase === 'build' && counted) {
        return { text: `Converting ${done}/${total}`, title: `Docling has converted ${done} of ${total} pages.` };
    }
    if (phase === 'assemble') {
        return { text: 'Assembling', title: 'Docling has read every page and is putting the document together.' };
    }
    if (phase === 'enrich' && counted) {
        return { text: `Enriching ${done}/${total}`, title: `Docling is adding ${label || 'details'}: ${done} of ${total} done.` };
    }
    return { text: 'Converting', title: 'Docling is converting your document — this can take a few minutes.' };
}

export default function ConvertButton({ theme, state, onClick, pageCount, error, progress }) {
    if (state === 'uploading') {
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title="Uploading PDF to the server…"
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                Uploading
            </button>
        );
    }
    if (state === 'converting') {
        const { text, title } = converting(progress);
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title={title}
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                {text}
            </button>
        );
    }
    if (state === 'converted') {
        return (
            <button
                onClick={onClick}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} text-emerald-500 hover:text-emerald-600 flex items-center`}
                title={`Converted${pageCount ? ` (${pageCount} pages)` : ''}. Click to reconvert with new options.`}
            >
                <CheckCircle2 size={14} className="inline mr-1" />
                Converted
            </button>
        );
    }
    if (state === 'failed' || state === 'conversion_failed') {
        return (
            <button
                onClick={onClick}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} text-red-500 hover:text-red-600 flex items-center`}
                title={error ? `Conversion failed: ${error}` : 'Conversion failed — click to retry'}
            >
                <AlertTriangle size={14} className="inline mr-1" />
                Retry convert
            </button>
        );
    }
    return (
        <button
            onClick={onClick}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.hover} ${theme.textSecondary} hover:text-blue-500 flex items-center`}
            title="Convert this PDF to clean Markdown using Docling (layout-aware, table-friendly)."
        >
            <FileText size={14} className="inline mr-1" />
            Convert
        </button>
    );
}
