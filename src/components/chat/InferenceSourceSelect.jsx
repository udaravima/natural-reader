// Segmented control: run inference through the authenticated backend gateway
// (default) or directly against a local Ollama (pre-gateway behavior).
export function InferenceSourceSelect({ source, onChange, theme }) {
    const opt = (value, label) => (
        <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            className={`flex-1 text-[10px] font-bold px-2 py-1.5 rounded-lg transition-colors ${
                source === value
                    ? 'bg-blue-500 text-white'
                    : `${theme.textSecondary} ${theme.hover}`
            }`}
        >
            {label}
        </button>
    );
    return (
        <div className={`flex gap-1 p-1 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
            {opt('server', 'Server')}
            {opt('local', 'Local Ollama')}
        </div>
    );
}
