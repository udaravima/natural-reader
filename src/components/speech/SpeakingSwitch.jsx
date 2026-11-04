import { useRef, useState } from 'react';
import { ChevronDown, Languages } from 'lucide-react';
import { useMenuDismiss } from '../../hooks/useMenuDismiss';

/**
 * The language you speak, one tap away where you speak (spec 2026-11-04-speech-choices-per-person): beside the
 * microphone and in Talk. A chip with the language's name; the menu lists `options` (lib/speech/speaking.js), each
 * with what hears it, and All languages… opens Settings for the rest. It changes the person's Speech language, the
 * same value as Settings → Chat; the next clip goes in it. Hidden when there is nothing to choose but Auto.
 *
 * `autoMeans`: the language Auto is sent as here (Talk sends its window language), shown as "Auto (English)". Without
 * it, an Auto option's `means` (the one language Google's first recognizer hears) shows the same way.
 */
export function SpeakingSwitch({ theme, value, options, onChange, onMore = null, autoMeans = null, className = '' }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const openerRef = useRef(null);
  useMenuDismiss({ open, onClose: () => setOpen(false), wrapRef, openerRef });
  if (!options || options.length < 2) return null;
  const current = options.find((o) => o.code === value) ?? options[0];
  // Talk's own language first: Talk sends it on Auto. Else the one language Auto can be heard in, if any.
  const auto = autoMeans ?? current.means;
  const chip = current.code === 'auto' && auto ? `Auto (${auto})` : current.label;
  return (
    <div ref={wrapRef} data-own-keys className={`relative ${className}`}>
      <button
        type="button"
        ref={openerRef}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Speaking: ${chip}`}
        title="The language you speak"
        className={`min-h-11 flex items-center gap-1.5 px-3 rounded-xl border text-xs font-bold ${theme.border} ${theme.bgTertiary} ${theme.text} ${theme.hover}`}
      >
        <Languages size={16} aria-hidden="true" className={theme.textSecondary} />
        <span className="max-w-[8rem] truncate">{chip}</span>
        <ChevronDown size={12} aria-hidden="true" className={theme.textSecondary} />
      </button>
      {open && (
        // Above the chip: it sits at the bottom of the screen, beside the message box or in the Talk panel.
        <div role="menu" aria-label="The language you speak"
          className={`absolute bottom-full mb-2 left-0 w-64 max-h-80 overflow-y-auto rounded-xl shadow-2xl border ${theme.border} ${theme.bgSecondary} z-40`}>
          {options.map((o) => (
            <button
              key={o.code}
              type="button"
              role="menuitemradio"
              aria-checked={o.code === current.code}
              onClick={() => { setOpen(false); onChange(o.code); }}
              className={`w-full flex flex-col px-4 py-2.5 text-left ${theme.hover} ${o.code === current.code ? 'text-blue-500' : theme.text}`}
            >
              <span className="text-sm font-semibold">{o.label}</span>
              {o.detail && <span className={`text-[10px] ${theme.textMuted}`}>{o.detail}</span>}
            </button>
          ))}
          {onMore && (
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onMore(); }}
              className={`w-full px-4 py-2.5 text-left text-xs font-bold border-t ${theme.border} ${theme.hover} ${theme.textSecondary}`}>
              All languages…
            </button>
          )}
        </div>
      )}
    </div>
  );
}
