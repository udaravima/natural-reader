// A labeled <select> row for a single per-model inference parameter. Shared by
// the Settings page's Chat & Inference section (and formerly the chat sidebar).
// `options` is an array of [value, label] pairs; `onChange` receives the value.
export function InferenceRow({ theme, label, value, onChange, options, disabled = false }) {
    // Whitespace is invalid in an HTML id and breaks `querySelector('#…')` —
    // slugify the label instead of interpolating it raw.
    const id = `inf-${label.toLowerCase().replace(/\s+/g, '-')}`;
    return (
        <div className="flex items-center gap-2">
            <label className={`text-[10px] font-bold ${theme.textMuted} flex-1 min-w-0 truncate`} htmlFor={id}>
                {label}
            </label>
            <select
                id={id}
                aria-label={label}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                disabled={disabled}
                className={`text-[11px] font-bold p-1.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors w-32 shrink-0 ${disabled ? 'opacity-60' : ''}`}
            >
                {options.map(([val, text]) => (
                    <option key={val} value={val}>{text}</option>
                ))}
            </select>
        </div>
    );
}
