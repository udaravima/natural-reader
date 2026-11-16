/**
 * An empty chat asks who the person wants to talk to (character profiles spec, "Choosing in the chat"): one card
 * per profile an admin left on, the current one pressed. Typing without picking uses the default, as before.
 * Nothing when there is no choice. The cards wrap, so a phone stacks them.
 */
export default function ProfileCards({ theme, profiles = [], profileId = '', onPick }) {
    if (profiles.length < 2) return null;
    return (
        <div className="w-full max-w-xl flex flex-col gap-2">
            <p className={`text-sm font-semibold ${theme.textSecondary}`}>Who would you like to talk to?</p>
            <div className="flex flex-wrap gap-2">
                {profiles.map((p) => {
                    const current = p.id === profileId;
                    return (
                        <button key={p.id} type="button" aria-pressed={current} onClick={() => onPick?.(p.id)}
                            className={`basis-56 grow min-w-0 flex flex-col items-start gap-0.5 p-3 rounded-xl border text-left transition-colors ${current ? 'border-blue-500 bg-blue-500/10' : `${theme.border} ${theme.bgSecondary} hover:border-blue-400`}`}>
                            <span className={`max-w-full truncate text-xs font-bold ${theme.text}`}>
                                {p.name}{p.isDefault && <span className={`font-normal ${theme.textMuted}`}> (default)</span>}
                            </span>
                            {p.description && <span className={`text-[11px] ${theme.textMuted}`}>{p.description}</span>}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
