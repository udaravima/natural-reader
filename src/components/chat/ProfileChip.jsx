import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, UserRound } from 'lucide-react';

const menuItems = (menu) => [...(menu?.querySelectorAll('[role="menuitemradio"]') ?? [])];

/**
 * Who answers in this chat (character profiles spec, "Choosing in the chat"): a chip beside "What this chat is
 * about", so Chat view, the chat beside the page and Talk all show it. With a choice it is a menu button (Enter,
 * Space or the arrows open it, the arrows move, Escape closes it, as ScopeChip); with one profile it is a label.
 *
 * `profiles`: the ones an admin left on ([{id, name, description, isDefault}]). `profileId`: what the next message
 * uses. `currentProfile`: the chat's own ({id, name}), listed as "(turned off)" when it is no longer offered.
 * `removedProfile`: the name of a profile this chat used that was deleted. `onChange(id)`: a pick.
 */
export default function ProfileChip({ theme, profiles = [], profileId = '', currentProfile = null,
    removedProfile = null, onChange }) {
    const [open, setOpen] = useState(false);
    const rootRef = useRef(null);
    const buttonRef = useRef(null);
    const menuRef = useRef(null);
    const menuId = useId();

    const choices = useMemo(() => {
        const listed = profiles.map((p) => ({ id: p.id, name: p.name, description: p.description || '',
            label: p.isDefault ? `${p.name} (default)` : p.name }));
        return currentProfile?.id && !profiles.some((p) => p.id === currentProfile.id)
            ? [...listed, { id: currentProfile.id, name: currentProfile.name, description: '',
                label: `${currentProfile.name} (turned off)` }]
            : listed;
    }, [profiles, currentProfile]);
    // Never an empty name: what the next message uses, else the chat's own, else the default.
    const shown = choices.find((c) => c.id === profileId)
        ?? choices.find((c) => c.id === currentProfile?.id)
        ?? choices.find((c) => profiles.find((p) => p.id === c.id)?.isDefault)
        ?? choices[0];

    const close = useCallback((refocus) => {
        setOpen(false);
        if (refocus) buttonRef.current?.focus();
    }, []);

    useEffect(() => {
        if (!open) return;
        const all = menuItems(menuRef.current);
        (all.find((el) => el.getAttribute('aria-checked') === 'true') ?? all[0])?.focus();
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (!rootRef.current?.contains(e.target)) close(false); };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open, close]);

    const onKeyDown = (e) => {
        // Space and Escape are the reader's narration shortcuts on window: keep them on the chip.
        if (e.key === ' ' || e.key === 'Enter' || e.key === 'Escape') e.stopPropagation();
        if (!open) {
            if (e.target === buttonRef.current && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.preventDefault();
                setOpen(true);
            }
            return;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            close(true);
        } else if (e.key === 'Tab') {
            close(false);
        } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
            e.preventDefault();
            const all = menuItems(menuRef.current);
            if (all.length === 0) return;
            const at = all.indexOf(document.activeElement);
            const next = e.key === 'Home' ? 0
                : e.key === 'End' ? all.length - 1
                : e.key === 'ArrowDown' ? (at + 1) % all.length
                : (at < 0 ? all.length - 1 : (at - 1 + all.length) % all.length);
            all[next].focus();
        }
    };

    if (!shown) return null;
    const label = `Talking to: ${shown.name}`;
    const chip = `max-w-full flex items-center gap-2 px-3 py-1.5 rounded-full border text-[11px] ${theme.border} ${theme.bgTertiary} ${theme.text}`;
    const removed = removedProfile && (
        <p className={`text-[10px] px-1 ${theme.textMuted}`}>The profile this chat used, {removedProfile}, was removed.</p>
    );

    if (choices.length < 2) {
        return (
            <div className="flex flex-col gap-1 self-start max-w-full">
                <span className={chip} title={shown.description || undefined}>
                    <UserRound size={12} aria-hidden="true" className={`shrink-0 ${theme.textMuted}`} />
                    <span className="truncate max-w-[16rem]">{label}</span>
                </span>
                {removed}
            </div>
        );
    }

    return (
        <div ref={rootRef} className="relative flex flex-col gap-1 self-start max-w-full" onKeyDown={onKeyDown}>
            <button
                ref={buttonRef}
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                title={shown.description || 'Who answers in this chat'}
                onClick={() => (open ? close(false) : setOpen(true))}
                className={`${chip} outline-none focus-visible:ring-2 focus-visible:ring-blue-500`}
            >
                <UserRound size={12} aria-hidden="true" className="shrink-0 text-blue-500" />
                <span className="truncate max-w-[16rem]">{label}</span>
                <ChevronDown size={12} aria-hidden="true" className={`shrink-0 ${theme.textMuted}`} />
            </button>
            {open && (
                <div className={`absolute left-0 bottom-full mb-1 z-30 w-72 max-w-[calc(100vw-2rem)] max-h-72 overflow-y-auto rounded-xl border shadow-lg p-1 ${theme.border} ${theme.bgSecondary}`}>
                    <div ref={menuRef} id={menuId} role="menu" aria-label="Who answers in this chat" className="flex flex-col">
                        {choices.map((c) => {
                            const current = c.id === shown.id;
                            return (
                                <button key={c.id} type="button" role="menuitemradio" tabIndex={-1} aria-checked={current}
                                    title={c.description || undefined}
                                    onClick={() => { close(true); if (!current) onChange?.(c.id); }}
                                    className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[11px] outline-none hover:bg-blue-500/10 focus:bg-blue-500/15 focus:ring-1 focus:ring-inset focus:ring-blue-500 ${theme.text} ${current ? 'font-bold' : ''}`}>
                                    <span className="flex-1 min-w-0 truncate">{c.label}</span>
                                    {current && <Check size={12} aria-hidden="true" className="shrink-0 text-blue-500" />}
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}
            {removed}
        </div>
    );
}
