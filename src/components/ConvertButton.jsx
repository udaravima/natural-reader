import { FileText, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';

/**
 * Toolbar button that drives the docling Convert-to-Markdown flow.
 *
 * State is owned by the parent (App.jsx) — this component just renders the
 * appropriate label/icon and fires `onClick` when interactive. Mirrors the
 * shape of `IndexButton` so the toolbar stays visually consistent.
 *
 * states: 'idle' | 'uploading' | 'converting' | 'converted' | 'failed'
 */
export default function ConvertButton({ theme, state, onClick, pageCount, error }) {
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
        return (
            <button
                disabled
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${theme.bgTertiary} ${theme.textSecondary} opacity-80 cursor-wait flex items-center`}
                title="Docling is converting your document — this can take a few minutes."
            >
                <Loader2 size={14} className="inline mr-1 animate-spin" />
                Converting
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
    if (state === 'failed') {
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
