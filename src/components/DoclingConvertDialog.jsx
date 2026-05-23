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

function deriveInitial(initialOptions, pageCount) {
    const opts = initialOptions || {};
    const pr = opts.page_range;
    const useRange = Array.isArray(pr) && pr.length === 2;
    return {
        preset: opts.preset || 'standard',
        ocr: !!opts.ocr,
        tables: opts.tables == null ? true : !!opts.tables,
        images: opts.images || 'drop',
        useRange,
        rangeStart: useRange ? pr[0] : 1,
        rangeEnd: useRange ? pr[1] : (pageCount || 1),
    };
}

export default function DoclingConvertDialog({
    theme,
    darkMode,
    open,
    onClose,
    onSubmit,
    pageCount,
    initialOptions,
}) {
    // React-recommended pattern for "reset state when prop changes": store the
    // open/initial inputs in state and reset during render when they flip.
    // Avoids cascading-render warnings from a setState-in-effect approach.
    const [prevOpen, setPrevOpen] = useState(open);
    const [form, setForm] = useState(() => deriveInitial(initialOptions, pageCount));
    if (open && !prevOpen) {
        setPrevOpen(true);
        setForm(deriveInitial(initialOptions, pageCount));
    } else if (!open && prevOpen) {
        setPrevOpen(false);
    }

    const update = (patch) => setForm((prev) => ({ ...prev, ...patch }));

    if (!open) return null;

    const total = pageCount || 1;
    const clampedStart = Math.max(1, Math.min(form.rangeStart || 1, total));
    const clampedEnd = Math.max(clampedStart, Math.min(form.rangeEnd || total, total));

    const handleSubmit = () => {
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
                                const active = form.preset === p.key;
                                const Icon = p.Icon;
                                return (
                                    <button
                                        key={p.key}
                                        onClick={() => update({ preset: p.key })}
                                        className={`flex flex-col items-start gap-1 p-3 rounded-lg border text-left transition-all ${
                                            active
                                                ? 'border-blue-500 bg-blue-500/10'
                                                : `${fieldBorder} ${fieldBg} ${theme.hover}`
                                        }`}
                                    >
                                        <Icon size={16} className={active ? 'text-blue-500' : theme.textSecondary} />
                                        <span className="text-sm font-bold">{p.label}</span>
                                        <span className={`text-[11px] leading-tight ${theme.textMuted}`}>{p.blurb}</span>
                                    </button>
                                );
                            })}
                        </div>
                    </section>

                    <section className="space-y-2">
                        <label className={`flex items-center justify-between p-3 rounded-lg ${fieldBg} border ${fieldBorder} cursor-pointer`}>
                            <span>
                                <span className="text-sm font-bold">Force OCR</span>
                                <span className={`block text-xs ${theme.textMuted}`}>
                                    Run OCR even when text is already extractable. Needed for scanned PDFs.
                                </span>
                            </span>
                            <input
                                type="checkbox"
                                checked={form.ocr}
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
                                const active = form.images === key;
                                return (
                                    <button
                                        key={key}
                                        onClick={() => update({ images: key })}
                                        className={`p-2 rounded-lg border text-left transition-all ${
                                            active
                                                ? 'border-blue-500 bg-blue-500/10'
                                                : `${fieldBorder} ${fieldBg} ${theme.hover}`
                                        }`}
                                    >
                                        <div className="text-sm font-bold">{label}</div>
                                        <div className={`text-[11px] ${theme.textMuted}`}>{blurb}</div>
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

                <footer className={`flex items-center justify-end gap-2 px-5 py-3 border-t ${fieldBorder}`}>
                    <button
                        onClick={onClose}
                        className={`px-3 py-1.5 rounded-lg text-sm font-bold ${theme.hover} ${theme.textSecondary}`}
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        className="px-4 py-1.5 rounded-lg text-sm font-bold bg-blue-600 text-white hover:bg-blue-700 transition-all"
                    >
                        Convert
                    </button>
                </footer>
            </div>
        </div>
    );
}
