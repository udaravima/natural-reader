import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Shows exactly the text a staged text file will send: the masked text, or the
 * original once the person has chosen real values. Follows SettingsDialog's
 * dialog pattern: Escape, the backdrop and the close button close it, Tab stays
 * inside, and focus goes back to the chip that opened it.
 *
 * "Send real values" (offered only while values are masked) asks first, in the
 * same dialog; the answer lives in this component's state, so closing it by any
 * route leaves nothing half-confirmed. `modelName` and `isCloud` word the
 * question truthfully: the text goes to that model, and to a cloud service when
 * the model runs on one.
 */
export default function TextFileViewer({ attachment, theme, modelName, isCloud, onSendReal, onClose }) {
    const boxRef = useRef(null);
    const [asking, setAsking] = useState(false);
    const done = useRef(false);

    useEffect(() => {
        const opener = document.activeElement;
        boxRef.current?.focus();
        return () => { if (opener && typeof opener.focus === 'function') opener.focus(); };
    }, []);

    if (!attachment) return null;
    const masked = !attachment.sendReal && attachment.masked > 0;
    const shown = attachment.sendReal ? attachment.originalText : attachment.maskedText;

    const onKeyDown = (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
        if (e.key !== 'Tab') return;
        const items = [...boxRef.current.querySelectorAll(FOCUSABLE)];
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };

    const confirm = () => {
        if (done.current) return;      // a second click does nothing
        done.current = true;
        onSendReal();
        onClose();
    };

    return (
        <div
            className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
            onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                ref={boxRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="text-file-title"
                tabIndex={-1}
                onKeyDown={onKeyDown}
                className={`outline-none w-full max-w-2xl max-h-[85vh] flex flex-col rounded-2xl shadow-2xl border ${theme.border} ${theme.bg} ${theme.text}`}
            >
                <div className="flex items-center justify-between gap-2 px-4 py-3">
                    <h1 id="text-file-title" className="text-sm font-bold truncate">{attachment.name}</h1>
                    <button onClick={onClose} aria-label="Close" title="Close (Esc)" className={`p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary}`}>
                        <X size={16} />
                    </button>
                </div>
                <pre className={`mx-4 mb-3 p-3 rounded-lg border ${theme.border} ${theme.bgTertiary} text-xs overflow-auto whitespace-pre-wrap break-words flex-1`}>{shown}</pre>
                {masked && !asking && (
                    <div className="px-4 pb-4 flex items-center justify-between gap-3">
                        <span className={`text-[11px] ${theme.textMuted}`}>
                            {attachment.masked === 1 ? '1 value is masked.' : `${attachment.masked} values are masked.`}
                        </span>
                        <button onClick={() => setAsking(true)} className="text-xs font-bold px-3 py-1.5 rounded-lg border border-amber-500 text-amber-600 hover:bg-amber-500/10">
                            Send real values
                        </button>
                    </div>
                )}
                {masked && asking && (
                    <div className="px-4 pb-4 flex flex-col gap-3">
                        <p className="text-xs font-bold">
                            {isCloud
                                ? `The real values will be sent to ${modelName}, which runs on a cloud service, and saved in this chat.`
                                : `The real values will be sent to ${modelName} and saved in this chat.`}
                        </p>
                        <div className="flex justify-end gap-2">
                            <button onClick={() => setAsking(false)} className={`text-xs font-bold px-3 py-1.5 rounded-lg ${theme.hover} ${theme.textSecondary}`}>Keep masked</button>
                            <button onClick={confirm} className="text-xs font-bold px-3 py-1.5 rounded-lg bg-amber-600 text-white hover:bg-amber-700">Yes, send real values</button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
