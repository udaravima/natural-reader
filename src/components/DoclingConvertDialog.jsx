import { useState } from 'react';
import { X, Sparkles, Zap, Wand2 } from 'lucide-react';

/**
 * Modal that collects docling conversion options before kicking off a /convert
 * job. Stays purely controlled — the parent (App.jsx) owns the network call;
 * we just hand back the chosen `options` object on submit.
 */
const PRESETS = [
    {
        key: 'fast',
        label: 'Fast',
        Icon: Zap,
        blurb: 'Text + reading order only. Tables and OCR are skipped — best for clean, text-only PDFs.',
    },
    {
        key: 'standard',
        label: 'Standard',
        Icon: Sparkles,
        blurb: 'Layout + tables. Recommended for most PDFs.',
    },
    {
        key: 'accurate',
        label: 'Accurate',
        Icon: Wand2,
        blurb: 'Vision-language pipeline (GraniteDocling). Slowest, but handles figures and unusual layouts best.',
    },
];

const OFF_NOTE = 'Turned off by your admin';

// Move a form off any choice the admin has turned off: the first allowed
// preset, OCR off, images dropped. A null `limits` (not answered yet, or the
// call failed) changes nothing. With no preset allowed the preset is left
// alone: Convert is disabled then, so it is never sent.
function followLimits(form, limits) {
    if (!limits) return form;
    const presets = limits.presets || [];
    return {
        ...form,
        preset: presets.length === 0 || presets.includes(form.preset) ? form.preset : presets[0],
        ocr: limits.ocr ? form.ocr : false,
        images: form.images === 'describe' && !limits.picture_description ? 'drop' : form.images,
    };
}

function deriveInitial(initialOptions, pageCount, limits) {
    const opts = initialOptions || {};
    const pr = opts.page_range;
    const useRange = Array.isArray(pr) && pr.length === 2;
    return followLimits({
        preset: opts.preset || 'standard',
        ocr: !!opts.ocr,
        tables: opts.tables == null ? true : !!opts.tables,
        images: opts.images || 'drop',
        useRange,
        rangeStart: useRange ? pr[0] : 1,
        rangeEnd: useRange ? pr[1] : (pageCount || 1),
    }, limits);
}

export default function DoclingConvertDialog({
    theme,
    darkMode,
    open,
    onClose,
    onSubmit,
    pageCount,
    initialOptions,
    limits = null,
}) {
    // React-recommended pattern for "reset state when prop changes": store the
    // open/initial inputs in state and reset during render when they flip.
    // Avoids cascading-render warnings from a setState-in-effect approach.
    const [prevOpen, setPrevOpen] = useState(open);
    const [prevLimits, setPrevLimits] = useState(limits);
    const [form, setForm] = useState(() => deriveInitial(initialOptions, pageCount, limits));
    if (open && !prevOpen) {
        setPrevOpen(true);
        setPrevLimits(limits);
        setForm(deriveInitial(initialOptions, pageCount, limits));
    } else if (!open && prevOpen) {
        setPrevOpen(false);
    }
    // The limits arrive a moment after the dialog opens: move whatever the
    // reader hasn't yet changed off a turned-off choice once they do.
    if (limits !== prevLimits) {
        setPrevLimits(limits);
        setForm((prev) => followLimits(prev, limits));
    }

    const update = (patch) => setForm((prev) => ({ ...prev, ...patch }));

    if (!open) return null;

    const total = pageCount || 1;
    const clampedStart = Math.max(1, Math.min(form.rangeStart || 1, total));
    const clampedEnd = Math.max(clampedStart, Math.min(form.rangeEnd || total, total));

    const presetAllowed = (key) => !limits || (limits.presets || []).includes(key);
    const ocrAllowed = !limits || !!limits.ocr;
    const describeAllowed = !limits || !!limits.picture_description;

    // Why Convert is off, if it is. The server refuses these too; saying it here
    // saves the reader a round trip.
    const pagesToConvert = form.useRange ? clampedEnd - clampedStart + 1 : total;
    let blocker = null;
    if (limits && !limits.may_convert) {
        blocker = 'Only people your admin has given the Docling role can convert documents on this server.';
    } else if (limits && (limits.presets || []).length === 0) {
        blocker = "Your admin hasn't allowed any conversion preset.";
    } else if (limits && limits.max_pages > 0 && pagesToConvert > limits.max_pages) {
        blocker = `This server converts at most ${limits.max_pages} pages at a time. Choose a page range of ${limits.max_pages} pages or fewer.`;
    }

    const handleSubmit = () => {
        if (blocker) return;
        onSubmit({
            preset: form.preset,
            ocr: form.ocr,
            tables: form.tables,
            images: form.images,
            page_range: form.useRange ? [clampedStart, clampedEnd] : null,
        });
    };

    const panelBg = darkMode ? 'bg-slate-800' : 'bg-white';
    const fieldBg = darkMode ? 'bg-slate-900/60' : 'bg-slate-50';
    const fieldBorder = darkMode ? 'border-slate-700' : 'border-slate-300';

    return (
        <div
            className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={onClose}
        >
            <div
                className={`w-full max-w-lg rounded-xl shadow-2xl ${panelBg} ${theme.text} border ${fieldBorder} max-h-[90vh] overflow-y-auto`}
                onClick={(e) => e.stopPropagation()}
            >
                <header className={`flex items-center justify-between px-5 py-3 border-b ${fieldBorder}`}>
                    <div>
                        <h2 className="text-base font-bold">Convert to Markdown</h2>
                        <p className={`text-xs ${theme.textMuted}`}>Powered by Docling</p>
                    </div>
                    <button
                        onClick={onClose}
                        className={`p-1.5 rounded-lg ${theme.hover} ${theme.textSecondary}`}
                        title="Close"
                    >
                        <X size={16} />
                    </button>
                </header>

                <div className="px-5 py-4 space-y-5">
                    <section>
                        <label className="text-xs font-bold uppercase tracking-wide">Quality</label>
                        <div className="grid grid-cols-3 gap-2 mt-2">
                            {PRESETS.map((p) => {
                                const allowed = presetAllowed(p.key);
                                const active = allowed && form.preset === p.key;
                                const Icon = p.Icon;
                                return (
                                    <button
                                        key={p.key}
                                        onClick={() => update({ preset: p.key })}
                                        disabled={!allowed}
                                        className={`flex flex-col items-start gap-1 p-3 rounded-lg border text-left transition-all ${
                                            allowed ? '' : 'opacity-50 cursor-not-allowed'
                                        } ${
                                            active
                                                ? 'border-blue-500 bg-blue-500/10'
                                                : `${fieldBorder} ${fieldBg} ${allowed ? theme.hover : ''}`
                                        }`}
                                    >
                                        <Icon size={16} className={active ? 'text-blue-500' : theme.textSecondary} />
                                        <span className="text-sm font-bold">{p.label}</span>
                                        <span className={`text-[11px] leading-tight ${theme.textMuted}`}>{allowed ? p.blurb : OFF_NOTE}</span>
                                    </button>
                                );
                            })}
                        </div>
                    </section>

                    <section className="space-y-2">
                        <label className={`flex items-center justify-between p-3 rounded-lg ${fieldBg} border ${fieldBorder} ${ocrAllowed ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}`}>
                            <span>
                                <span className="text-sm font-bold">Force OCR</span>
                                <span className={`block text-xs ${theme.textMuted}`}>
                                    {ocrAllowed
                                        ? 'Run OCR even when text is already extractable. Needed for scanned PDFs.'
                                        : OFF_NOTE}
                                </span>
                            </span>
                            <input
                                type="checkbox"
                                checked={form.ocr}
                                disabled={!ocrAllowed}
                                onChange={(e) => update({ ocr: e.target.checked })}
                                className="h-4 w-4 accent-blue-500"
                            />
                        </label>
                        <label className={`flex items-center justify-between p-3 rounded-lg ${fieldBg} border ${fieldBorder} cursor-pointer`}>
                            <span>
                                <span className="text-sm font-bold">Extract tables</span>
                                <span className={`block text-xs ${theme.textMuted}`}>
                                    Reconstruct tables as Markdown. Disable for plain-text output.
                                </span>
                            </span>
                            <input
                                type="checkbox"
                                checked={form.tables}
                                onChange={(e) => update({ tables: e.target.checked })}
                                className="h-4 w-4 accent-blue-500"
                            />
                        </label>
                    </section>

                    <section>
                        <label className="text-xs font-bold uppercase tracking-wide">Images</label>
                        <div className="grid grid-cols-3 gap-2 mt-2">
                            {[
                                { key: 'drop', label: 'Drop', blurb: 'Smallest output' },
                                { key: 'embed', label: 'Embed', blurb: 'Inline base64' },
                                { key: 'describe', label: 'Describe', blurb: 'VLM caption' },
                            ].map(({ key, label, blurb }) => {
                                const allowed = key !== 'describe' || describeAllowed;
                                const active = form.images === key;
                                return (
                                    <button
                                        key={key}
                                        onClick={() => update({ images: key })}
                                        disabled={!allowed}
                                        className={`p-2 rounded-lg border text-left transition-all ${
                                            allowed ? '' : 'opacity-50 cursor-not-allowed'
                                        } ${
                                            active
                                                ? 'border-blue-500 bg-blue-500/10'
                                                : `${fieldBorder} ${fieldBg} ${allowed ? theme.hover : ''}`
                                        }`}
                                    >
                                        <div className="text-sm font-bold">{label}</div>
                                        <div className={`text-[11px] ${theme.textMuted}`}>{allowed ? blurb : OFF_NOTE}</div>
                                    </button>
                                );
                            })}
                        </div>
                    </section>

                    <section>
                        <label className={`flex items-center justify-between gap-2 p-3 rounded-lg ${fieldBg} border ${fieldBorder} cursor-pointer`}>
                            <span>
                                <span className="text-sm font-bold">Limit page range</span>
                                <span className={`block text-xs ${theme.textMuted}`}>
                                    Convert only a slice of the document ({total} pages total).
                                </span>
                            </span>
                            <input
                                type="checkbox"
                                checked={form.useRange}
                                onChange={(e) => update({ useRange: e.target.checked })}
                                className="h-4 w-4 accent-blue-500"
                            />
                        </label>
                        {form.useRange && (
                            <div className="flex items-center gap-2 mt-2">
                                <input
                                    type="number"
                                    min="1"
                                    max={total}
                                    value={form.rangeStart}
                                    onChange={(e) => update({ rangeStart: parseInt(e.target.value, 10) || 1 })}
                                    className={`w-20 px-2 py-1.5 rounded-md ${fieldBg} border ${fieldBorder} ${theme.text} text-center`}
                                />
                                <span className={theme.textMuted}>to</span>
                                <input
                                    type="number"
                                    min={clampedStart}
                                    max={total}
                                    value={form.rangeEnd}
                                    onChange={(e) => update({ rangeEnd: parseInt(e.target.value, 10) || total })}
                                    className={`w-20 px-2 py-1.5 rounded-md ${fieldBg} border ${fieldBorder} ${theme.text} text-center`}
                                />
                                <span className={`text-xs ${theme.textMuted}`}>of {total}</span>
                            </div>
                        )}
                    </section>
                </div>

                {blocker && (
                    <p role="alert" className="px-5 pb-3 text-sm text-amber-600">{blocker}</p>
                )}

                <footer className={`flex items-center justify-end gap-2 px-5 py-3 border-t ${fieldBorder}`}>
                    <button
                        onClick={onClose}
                        className={`px-3 py-1.5 rounded-lg text-sm font-bold ${theme.hover} ${theme.textSecondary}`}
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={!!blocker}
                        className="px-4 py-1.5 rounded-lg text-sm font-bold bg-blue-600 text-white hover:bg-blue-700 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-blue-600"
                    >
                        Convert
                    </button>
                </footer>
            </div>
        </div>
    );
}
